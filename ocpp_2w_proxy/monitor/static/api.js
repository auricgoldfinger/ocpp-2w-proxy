// REST calls to the monitor API (docs/monitor_api.md). Every route is a GET returning JSON.

/** `code` is the API's error code (`query_too_slow`, …), or null when the body named none. */
export class ApiError extends Error {
  constructor(status, path, code = null) {
    super(`${path} answered ${status}`);
    this.status = status;
    this.code = code;
  }
}

export async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new ApiError(response.status, path, await errorCode(response));
  return response.json();
}

async function errorCode(response) {
  try {
    return (await response.json()).error ?? null;
  } catch {
    return null; // not JSON: a proxy in between, say
  }
}

export const api = {
  config: () => getJson("/api/config"),
  chargers: () => getJson("/api/chargers"),
  /** `query` comes from filters.exchangesQuery. */
  exchanges: (query) => getJson(`/api/exchanges?${query}`),
  exchange: (id) => getJson(`/api/exchanges/${encodeURIComponent(id)}`),
  session: (tx) => getJson(`/api/sessions/${encodeURIComponent(tx)}`),
  links: () => getJson("/api/links"),
};
