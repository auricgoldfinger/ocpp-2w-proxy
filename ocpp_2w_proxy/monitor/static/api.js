// REST calls to the monitor API (docs/monitor_api.md). Every route is a GET returning JSON.

export class ApiError extends Error {
  constructor(status, path) {
    super(`${path} answered ${status}`);
    this.status = status;
  }
}

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new ApiError(response.status, path);
  return response.json();
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
