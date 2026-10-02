FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /bin/

WORKDIR /app

# Use the image's Python; compile bytecode at build time.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies first: cached as long as the lock file is unchanged.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY ocpp_2w_proxy ./ocpp_2w_proxy

ENV PATH="/app/.venv/bin:$PATH"

# 568 = the "apps" user on TrueNAS SCALE; give the /data dataset to this uid.
RUN mkdir -p /data /config && chown 568:568 /data
USER 568:568

EXPOSE 8321
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8321), 3).close()"

CMD ["python", "-m", "ocpp_2w_proxy", "--config", "/config/config.toml"]
