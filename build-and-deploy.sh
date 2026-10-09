#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ocpp-2w-proxy build + ship helper
#
# Subcommands:
#   test     Run pytest via uv (skippable via SKIP_TESTS=1)
#   build    podman build --platform=linux/amd64 -> ocpp-2w-proxy:$VERSION + :latest
#            from the Dockerfile (then prunes old dated tags + dangling images)
#   save     podman save   -> ocpp-2w-proxy-$VERSION.tar.gz in script dir
#   ship     scp the tarball to the NAS, ssh docker load, prune old NAS tags
#   all      test + build + save + ship (default)
#   clean    remove local *.tar.gz artefacts and prune old images
#
# Configuration (env vars or sourced from .env.build next to this script):
#   NAS_SSH_HOST   e.g. admin@truenas.local or admin@10.0.0.5
#   NAS_TMP_DIR    absolute path on the NAS the tarball gets copied to
#   VERSION        image tag (default: today's date, YYYY-MM-DD)
#   IMAGE_NAME     default: ocpp-2w-proxy
#   PLATFORM       default: linux/amd64
#   SKIP_TESTS     set to 1 to skip pytest on `build`/`all`
#
# The image is tagged fully qualified (docker.io/library/ocpp-2w-proxy) so
# `docker load` on the NAS yields exactly ocpp-2w-proxy:<tag>; podman's implicit
# localhost/ prefix would not match after loading. TrueNAS custom apps, however,
# rewrite the unqualified compose `image: ocpp-2w-proxy:latest` to
# localhost/ocpp-2w-proxy:latest, so `ship` also tags the loaded image under
# that name; the app finds it whichever way TrueNAS resolves it.
#
# On a linux/amd64 host (the NAS's architecture) the build runs natively.
# On Apple Silicon, podman builds linux/amd64 via the podman-machine VM + QEMU;
# the first build is slow (~2-3 min), later builds hit the layer cache and take
# seconds unless uv.lock changed.
# ---------------------------------------------------------------------------
set -euo pipefail

if ! command -v podman >/dev/null 2>&1; then
  printf '[!] podman not found; install Podman and try again\n' >&2
  exit 1
fi

if ! podman info >/dev/null 2>&1; then
  printf '[+] Podman is not accessible; starting machine\n'
  podman machine start
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Config ---------------------------------------------------------------

if [[ -f "$SCRIPT_DIR/.env.build" ]]; then
  # shellcheck disable=SC1091
  set -a; . "$SCRIPT_DIR/.env.build"; set +a
fi

IMAGE_NAME="${IMAGE_NAME:-ocpp-2w-proxy}"
VERSION="${VERSION:-$(date +%Y-%m-%d)}"
PLATFORM="${PLATFORM:-linux/amd64}"
FQN_IMAGE="docker.io/library/${IMAGE_NAME}"
TARBALL="${IMAGE_NAME}-${VERSION}.tar.gz"
CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
REVISION="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo "nogit")"

# ---- Pretty logging -------------------------------------------------------

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi
info()  { printf '%s[+] %s%s\n' "$BLUE"   "$*" "$RESET"; }
ok()    { printf '%s[✓] %s%s\n' "$GREEN"  "$*" "$RESET"; }
warn()  { printf '%s[!] %s%s\n' "$YELLOW" "$*" "$RESET" >&2; }
err()   { printf '%s[✗] %s%s\n' "$RED"    "$*" "$RESET" >&2; }

# ---- Subcommands ----------------------------------------------------------

cmd_test() {
  if [[ "${SKIP_TESTS:-0}" == "1" ]]; then
    warn "SKIP_TESTS=1 - skipping pytest"
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    err "uv not found; install it (see README.md) or set SKIP_TESTS=1"
    exit 1
  fi
  info "Running test suite (config, policy, routing, backends, integration)..."
  (cd "$SCRIPT_DIR" && uv run pytest tests/ -v)
  ok "Tests passed"
}

cmd_build() {
  cmd_test
  info "Building ${BOLD}${IMAGE_NAME}:${VERSION}${RESET}${BLUE} for ${PLATFORM}"
  podman build \
    --platform="$PLATFORM" \
    --format=docker \
    --build-arg "IMAGE_VERSION=${VERSION}" \
    --build-arg "IMAGE_REVISION=${REVISION}" \
    --build-arg "IMAGE_CREATED=${CREATED}" \
    -t "${FQN_IMAGE}:${VERSION}" \
    -t "${FQN_IMAGE}:latest" \
    -f Dockerfile \
    .
  ok "Built ${IMAGE_NAME}:${VERSION} (+ :latest)"
  podman image inspect "${FQN_IMAGE}:${VERSION}" \
    --format 'Architecture: {{.Architecture}}  OS: {{.Os}}  Size: {{.Size}} bytes'
  prune_local_images
}

# Remove old dated tags (keeping :latest and :$VERSION) and dangling images.
# Images in use by a container are never removed; failures are non-fatal.
prune_local_images() {
  info "Pruning old ${IMAGE_NAME} tags and dangling images"
  local tag
  while IFS= read -r tag; do
    [[ -z "$tag" || "$tag" == "latest" || "$tag" == "$VERSION" || "$tag" == "<none>" ]] && continue
    podman rmi "${FQN_IMAGE}:${tag}" >/dev/null 2>&1 \
      && info "  removed ${IMAGE_NAME}:${tag}" \
      || warn "  could not remove ${IMAGE_NAME}:${tag} (in use?)"
  done < <(podman images --format '{{.Tag}}' "${FQN_IMAGE}" 2>/dev/null || true)
  podman image prune -f >/dev/null 2>&1 || warn "podman image prune failed"
  ok "Pruned"
}


cmd_save() {
  info "Saving image to ${BOLD}${TARBALL}${RESET}"
  # Save as OCI/docker archive, then gzip. `podman save --format=docker-archive`
  # produces something `docker load` on the NAS reads without any manifest
  # translation. We gzip separately for portability (some podman builds on
  # macOS ship a save that can't emit gzip directly).
  local raw="${IMAGE_NAME}-${VERSION}.tar"
  podman save \
    --format=docker-archive \
    -o "$raw" \
    "${FQN_IMAGE}:${VERSION}" \
    "${FQN_IMAGE}:latest"
  gzip -f "$raw"
  ok "Wrote $TARBALL ($(du -h "$TARBALL" | cut -f1))"
}

cmd_ship() {
  : "${NAS_SSH_HOST:?NAS_SSH_HOST not set (see .env.build.example)}"
  : "${NAS_TMP_DIR:?NAS_TMP_DIR not set (see .env.build.example)}"

  if [[ ! -f "$TARBALL" ]]; then
    err "$TARBALL not found; run './build-and-deploy.sh save' first (or './build-and-deploy.sh all')"
    exit 1
  fi

  info "Ensuring ${NAS_TMP_DIR} exists on ${NAS_SSH_HOST}"
  ssh "$NAS_SSH_HOST" "mkdir -p '$NAS_TMP_DIR'"

  info "Copying $TARBALL -> ${NAS_SSH_HOST}:${NAS_TMP_DIR}/"
  scp "$TARBALL" "${NAS_SSH_HOST}:${NAS_TMP_DIR}/${TARBALL}"

  info "Loading image into Docker on the NAS (sudo password may be required once)"
  # Single `ssh -t` session with commands passed as an argument (NOT via a
  # heredoc on stdin — that breaks sudo's password prompt because the PTY
  # ends up being driven by the heredoc instead of your real terminal, and
  # the password gets echoed in cleartext / consumed as heredoc data).
  #
  # The first `sudo docker load` prompts once; the later `sudo` calls reuse the
  # cached sudo ticket (same PTY, within sudo's default grace window). `&&`
  # chaining aborts the rest if any step fails. The `docker tag` runs before
  # the prune so the image it superseded turns dangling and is removed too.
  ssh -t "$NAS_SSH_HOST" "\
    echo '[+] docker load' && \
    sudo docker load -i '${NAS_TMP_DIR}/${TARBALL}' && \
    echo '[+] tagging localhost/${IMAGE_NAME}:latest (TrueNAS custom apps reference it that way)' && \
    sudo docker tag '${IMAGE_NAME}:latest' 'localhost/${IMAGE_NAME}:latest' && \
    echo '[+] pruning old tags / dangling images / tarball' && \
    { for t in \$(sudo docker image ls '${IMAGE_NAME}' --format '{{.Tag}}'); do \
        case \"\$t\" in latest|'${VERSION}'|'<none>') continue;; esac; \
        sudo docker rmi '${IMAGE_NAME}':\"\$t\" >/dev/null 2>&1 \
          && echo \"  removed ${IMAGE_NAME}:\$t\" \
          || echo \"  could not remove ${IMAGE_NAME}:\$t (in use?)\"; \
      done; \
      sudo docker image prune -f >/dev/null 2>&1 || true; \
      rm -f '${NAS_TMP_DIR}/${TARBALL}'; } && \
    echo '[+] docker image ls' && \
    sudo docker image ls --format '  {{.Repository}}:{{.Tag}}  {{.CreatedSince}}  {{.Size}}' | grep '${IMAGE_NAME}'"

  ok "Shipped ${IMAGE_NAME}:${VERSION} (+ :latest) to ${NAS_SSH_HOST}"
  cat <<EOF

${DIM}Next step: in TrueNAS Scale, Apps -> ocpp-2w-proxy -> Stop then Start
(so the app picks up the refreshed :latest image). For a brand-new install,
see README.md ("Deploy on TrueNAS SCALE") and compose.yaml.${RESET}
EOF
}

cmd_all() {
  cmd_build
  cmd_save
  cmd_ship
}

cmd_clean() {
  info "Removing local tarballs"
  rm -f -- *.tar *.tar.gz
  prune_local_images
  ok "Clean"
}

# ---- Entrypoint -----------------------------------------------------------

usage() {
  sed -n '2,31p' "$0"
  exit "${1:-0}"
}

sub="${1:-all}"
shift || true

case "$sub" in
  test)   cmd_test   "$@" ;;
  build)  cmd_build  "$@" ;;
  save)   cmd_save   "$@" ;;
  ship)   cmd_ship   "$@" ;;
  all)    cmd_all    "$@" ;;
  clean)  cmd_clean  "$@" ;;
  -h|--help|help)  usage 0 ;;
  *)      err "Unknown subcommand: $sub"; usage 1 ;;
esac
