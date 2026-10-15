// Static topology header: charger → proxy → each backend, as an inline SVG string.
// Link state is drawn (solid up, dotted down) and also written as text.

import { formatDuration, formatRate } from "../format.js";
import { html } from "../html.js";

const WIDTH = 720;
const ROW = 52;
const NODE_H = 40;
const CHARGER = { x: 8, w: 150 };
const PROXY = { x: 270, w: 120 };
const PEER = { x: 520, w: 192 };

function peersOf(config, charger) {
  const live = new Map((charger?.peers ?? []).map((peer) => [peer.name, peer]));
  const names = [config.primary, ...config.secondaries.map((secondary) => secondary.name)];
  return names.map((name) => ({ name, primary: name === config.primary, live: live.get(name) ?? null }));
}

function stateText(up, since, now) {
  if (up === null) return "? unknown";
  const age = since ? ` ${formatDuration(now - since)}` : "";
  return up ? `● up${age}` : `✕ down${age}`;
}

function stateClass(up) {
  if (up === null) return "unknown";
  return up ? "up" : "down";
}

function node({ x, y, w, label, sub, classes, href }) {
  const box = html`<g class="node ${classes}"><rect x="${x}" y="${y}" width="${w}" height="${NODE_H}" rx="4"></rect><text class="node__label" x="${x + 10}" y="${y + 17}">${label}</text><text class="node__sub" x="${x + 10}" y="${y + 32}">${sub}</text></g>`;
  return href ? html`<a href="${href}" class="node-link"><title>Show exchanges with ${label}</title>${box}</a>` : box;
}

function edge({ x1, y1, x2, y2, up, label }) {
  const midX = (x1 + x2) / 2;
  // The label sits just before the target node, where the fanned-out edges no longer overlap.
  return html`<g class="edge edge--${stateClass(up)}"><path d="M${x1} ${y1} C${midX} ${y1} ${midX} ${y2} ${x2} ${y2}"></path><text class="edge__label" x="${x2 - 6}" y="${y2 - 6}" text-anchor="end">${label}</text></g>`;
}

/**
 * The topology SVG. `charger` is that charger's entry from /api/links (or null before it loads);
 * `hrefFor(peer)` gives the link that filters the list by that peer.
 */
export function topology(config, charger, { now, hrefFor }) {
  const peers = peersOf(config, charger);
  const height = Math.max(peers.length, 1) * ROW + 8;
  const midY = height / 2 - NODE_H / 2;
  const queued = peers.reduce((sum, peer) => sum + (peer.live?.queued ?? 0), 0);
  const connected = charger ? charger.connected : null;

  const parts = [
    edge({
      x1: CHARGER.x + CHARGER.w, y1: midY + NODE_H / 2, x2: PROXY.x, y2: midY + NODE_H / 2,
      up: connected, label: stateText(connected, null, now),
    }),
  ];
  peers.forEach((peer, at) => {
    const y = 8 + at * ROW;
    const up = peer.live ? peer.live.up : null;
    const rate = peer.live ? formatRate(peer.live.messages_last_hour) : "";
    parts.push(edge({
      x1: PROXY.x + PROXY.w, y1: midY + NODE_H / 2, x2: PEER.x, y2: y + NODE_H / 2,
      up, label: up === false ? "✕ down" : rate,
    }));
    parts.push(node({
      x: PEER.x, y, w: PEER.w,
      label: peer.primary ? `PRIMARY · ${peer.name}` : peer.name,
      sub: `${stateText(up, peer.live?.since, now)}${peer.live?.queued ? ` · ⧗ ${peer.live.queued}` : ""}`,
      classes: `node--${peer.primary ? "primary" : "secondary"} node--${stateClass(up)}`,
      href: hrefFor(peer.name),
    }));
  });
  parts.push(node({
    x: CHARGER.x, y: midY, w: CHARGER.w, label: "Charger", sub: charger ? charger.id : (config.chargers[0] ?? ""),
    classes: `node--charger node--${stateClass(connected)}`, href: null,
  }));
  parts.push(node({ x: PROXY.x, y: midY, w: PROXY.w, label: "PROXY", sub: `⧗ ${queued} queued`, classes: "node--proxy", href: null }));

  const title = `Charger ${connected === false ? "disconnected" : "connected"}; ${peers.length} backends`;
  return html`<svg class="topology" viewBox="0 0 ${WIDTH} ${height}" preserveAspectRatio="xMinYMid meet"><title>${title}</title>${parts}</svg>`;
}
