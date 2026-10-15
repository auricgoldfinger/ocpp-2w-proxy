// Pure URL-hash state: which view is open and which filters apply. Everything lives in the hash,
// so deep links like #/exchange/<id>?peer=stats work and survive a reload.

export const RANGES = { "": null, "15m": 900, "1h": 3600, "24h": 86400, "7d": 604800 };

export const DEFAULT_FILTERS = Object.freeze({
  charger: "",
  action: "",
  outcome: "",
  peer: "",
  problem: false,
  tx: "",
  q: "",
  range: "",
  heartbeats: false,
});

const TEXT_KEYS = ["charger", "action", "outcome", "peer", "tx", "q"];
const VIEWS = new Set(["exchanges", "exchange", "links"]);

function parseFilters(params) {
  const filters = { ...DEFAULT_FILTERS };
  for (const key of TEXT_KEYS) filters[key] = (params.get(key) ?? "").trim();
  filters.problem = params.get("problem") === "1";
  filters.heartbeats = params.get("hb") === "1";
  const range = params.get("range") ?? "";
  filters.range = Object.hasOwn(RANGES, range) ? range : "";
  return filters;
}

/** "#/exchange/42?peer=stats" → {view: "exchange", id: "42", filters: {...}}. */
export function parseHash(hash) {
  const body = (hash ?? "").replace(/^#\/?/, "");
  const [path, query = ""] = body.split("?", 2);
  const [first = "", ...rest] = path.split("/");
  const view = VIEWS.has(first) ? first : "exchanges";
  const id = view === "exchange" && rest[0] ? decodeURIComponent(rest[0]) : null;
  return { view: view === "exchange" && !id ? "exchanges" : view, id, filters: parseFilters(new URLSearchParams(query)) };
}

function filterParams(filters) {
  const params = new URLSearchParams();
  for (const key of TEXT_KEYS) if (filters[key]) params.set(key, filters[key]);
  if (filters.problem) params.set("problem", "1");
  if (filters.range) params.set("range", filters.range);
  if (filters.heartbeats) params.set("hb", "1");
  return params;
}

/** The inverse of parseHash; default filters are left out so links stay short. */
export function buildHash({ view = "exchanges", id = null, filters = DEFAULT_FILTERS }) {
  const path = view === "exchange" ? `exchange/${encodeURIComponent(id)}` : view;
  const query = view === "links" ? "" : filterParams(filters).toString();
  return `#/${path}${query ? `?${query}` : ""}`;
}

/** The Exchanges list of transaction `tx` (the session view) with the other filters kept; "" clears it. */
export function sessionHash(filters, tx) {
  return buildHash({ view: "exchanges", filters: { ...filters, tx: tx === null || tx === undefined ? "" : String(tx) } });
}

/** Query string for GET /api/exchanges. */
export function exchangesQuery(filters, { now, before = null, limit = 200 } = {}) {
  const params = new URLSearchParams();
  for (const key of TEXT_KEYS) if (filters[key]) params.set(key, filters[key]);
  if (filters.problem) params.set("problem", "1");
  if (!filters.heartbeats) params.set("exclude", "Heartbeat");
  const seconds = RANGES[filters.range];
  if (seconds) params.set("since", String(now - seconds));
  if (before) params.set("before", before);
  params.set("limit", String(limit));
  return params.toString();
}

/** True when a live heartbeat is filtered out only because heartbeats are hidden. */
export function isHiddenHeartbeat(summary, filters) {
  return !filters.heartbeats && summary.action === "Heartbeat";
}

function matchesQuery(summary, q) {
  const needle = q.toLowerCase();
  const haystack = [summary.id, summary.id_tag, summary.transaction_id, ...summary.legs.map((leg) => leg.wire_id)];
  return haystack.some((value) => value !== null && value !== undefined && String(value).toLowerCase().includes(needle));
}

/** Client-side twin of the server filters, for summaries that arrive over SSE. */
export function matchesFilters(summary, filters, now) {
  if (isHiddenHeartbeat(summary, filters)) return false;
  if (filters.charger && summary.charger_id !== filters.charger) return false;
  if (filters.action && summary.action !== filters.action) return false;
  if (filters.outcome && summary.outcome !== filters.outcome) return false;
  if (filters.peer && !summary.legs.some((leg) => leg.peer === filters.peer)) return false;
  if (filters.problem && !summary.problem) return false;
  if (filters.tx && String(summary.transaction_id) !== filters.tx) return false;
  if (filters.q && !matchesQuery(summary, filters.q)) return false;
  const seconds = RANGES[filters.range];
  return !(seconds && summary.started_at < now - seconds);
}

/** How many filters differ from the defaults, for the "clear filters" affordance. */
export function activeFilterCount(filters) {
  return Object.keys(DEFAULT_FILTERS).filter((key) => filters[key] !== DEFAULT_FILTERS[key]).length;
}
