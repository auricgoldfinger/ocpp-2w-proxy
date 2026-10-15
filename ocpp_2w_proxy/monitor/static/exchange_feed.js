// Pure state of the Exchanges list: loaded summaries, gaps, the paging cursor and the live
// updates held back while the list is paused. No DOM, no network.

export const MAX_ROWS = 1000;

const gapKey = (gap) => `${gap.charger_id}:${gap.from_at}`;

export class ExchangeFeed {
  constructor({ maxRows = MAX_ROWS } = {}) {
    this.maxRows = maxRows;
    this.clear();
  }

  clear() {
    this.byId = new Map();
    this.gaps = new Map();
    this.next = null;
    this.hidden = 0;
    this.trimmed = false;
    this.pending = new Map();
  }

  /** Replaces everything with a first page from GET /api/exchanges. */
  reset(page) {
    this.clear();
    this.appendOlder(page);
  }

  /** Adds an older page; its cursor becomes the one for "load older". */
  appendOlder(page) {
    for (const summary of page.exchanges) if (!this.byId.has(summary.id)) this.byId.set(summary.id, summary);
    this.next = page.next;
    this.hidden = page.hidden?.Heartbeat ?? this.hidden;
  }

  get size() {
    return this.byId.size;
  }

  has(id) {
    return this.byId.has(id);
  }

  /** Applies a live summary now; returns "added" or "updated". */
  upsert(summary) {
    const known = this.byId.has(summary.id);
    this.byId.set(summary.id, summary);
    return known ? "updated" : "added";
  }

  /** Holds a live summary back while the list is paused. */
  hold(summary) {
    this.pending.set(summary.id, summary);
  }

  /** How many held summaries are new rows (the "▲ N new" pill). */
  get pendingNew() {
    let count = 0;
    for (const id of this.pending.keys()) if (!this.byId.has(id)) count += 1;
    return count;
  }

  /** Applies every held summary. */
  release() {
    for (const summary of this.pending.values()) this.upsert(summary);
    this.pending.clear();
  }

  countHiddenHeartbeat() {
    this.hidden += 1;
  }

  setGaps(gaps) {
    this.gaps = new Map(gaps.map((gap) => [gapKey(gap), gap]));
  }

  addGap(gap) {
    this.gaps.set(gapKey(gap), gap);
  }

  /** Newest first. */
  exchanges() {
    return [...this.byId.values()].sort((a, b) => b.started_at - a.started_at);
  }

  /**
   * Drops the oldest rows beyond maxRows. The server cursor then points past a hole, so it is
   * discarded and the list reports `trimmed`; older rows come back with a reload.
   */
  trim() {
    const excess = this.byId.size - this.maxRows;
    if (excess <= 0) return 0;
    for (const summary of this.exchanges().slice(-excess)) this.byId.delete(summary.id);
    this.next = null;
    this.trimmed = true;
    return excess;
  }

  /** Exchanges and the gaps inside the loaded time range, interleaved newest first. */
  items() {
    const exchanges = this.exchanges();
    const oldest = exchanges.at(-1)?.started_at ?? -Infinity;
    const complete = this.next === null && !this.trimmed;
    const gaps = [...this.gaps.values()].filter((gap) => complete || gap.to_at >= oldest);
    const items = [
      ...exchanges.map((summary) => ({ type: "exchange", at: summary.started_at, summary })),
      ...gaps.map((gap) => ({ type: "gap", at: gap.to_at, gap })),
    ];
    return items.sort((a, b) => b.at - a.at);
  }
}
