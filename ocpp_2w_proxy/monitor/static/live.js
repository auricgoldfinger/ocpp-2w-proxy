// The live event stream (GET /api/events). It fans SSE events out to subscribers, reports
// whether the stream is connected, and asks them to re-fetch whenever events may have been
// missed: after a `reset` from a too-slow client, and after every reconnect.

const EVENTS = ["exchange", "link", "gap"];
const CLOSED = 2; // EventSource.CLOSED; the browser gave up and will not retry by itself

export class LiveStream {
  constructor({ url = "/api/events", createSource = (target) => new EventSource(target), retryMs = 3000, timers = globalThis } = {}) {
    this.url = url;
    this.createSource = createSource;
    this.retryMs = retryMs;
    this.timers = timers;
    this.listeners = new Set();
    this.source = null;
    this.connected = false;
    this.openedBefore = false;
    this.retryTimer = null;
  }

  /** `listener` may have exchange, link, gap, resync and status(connected) methods. */
  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  start() {
    this.source = this.createSource(this.url);
    this.source.addEventListener("open", () => this.onOpen());
    this.source.addEventListener("error", () => this.onError());
    this.source.addEventListener("reset", () => this.restart());
    for (const name of EVENTS) {
      this.source.addEventListener(name, (event) => this.onData(name, event.data));
    }
  }

  stop() {
    this.timers.clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.source?.close();
    this.source = null;
  }

  restart() {
    this.stop();
    this.setConnected(false);
    this.start();
  }

  onOpen() {
    this.setConnected(true);
    if (this.openedBefore) this.emit("resync");
    this.openedBefore = true;
  }

  onError() {
    this.setConnected(false);
    if (this.source?.readyState === CLOSED && this.retryTimer === null) {
      this.retryTimer = this.timers.setTimeout(() => {
        this.retryTimer = null;
        this.restart();
      }, this.retryMs);
    }
  }

  onData(name, data) {
    let parsed;
    try {
      parsed = JSON.parse(data);
    } catch {
      return;
    }
    this.emit(name, parsed);
  }

  setConnected(connected) {
    if (this.connected === connected) return;
    this.connected = connected;
    this.emit("status", connected);
  }

  emit(name, value) {
    for (const listener of this.listeners) listener[name]?.(value);
  }
}
