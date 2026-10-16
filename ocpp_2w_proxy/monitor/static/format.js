// Pure display formatting for times, durations, rates and numbers.

function pad(value) {
  return String(value).padStart(2, "0");
}

/** Local wall-clock time of an epoch-seconds timestamp, "14:02:11". */
export function formatTime(epochSeconds) {
  if (epochSeconds === null || epochSeconds === undefined) return "–";
  const date = new Date(epochSeconds * 1000);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** Local date and time, "2025-10-15 14:02:11". */
export function formatDateTime(epochSeconds) {
  if (epochSeconds === null || epochSeconds === undefined) return "–";
  const date = new Date(epochSeconds * 1000);
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${day} ${formatTime(epochSeconds)}`;
}

/** An ISO 8601 timestamp as local date and time; text that is not one is returned as it is. */
export function formatIsoDateTime(text) {
  const millis = typeof text === "string" ? Date.parse(text) : Number.NaN;
  return Number.isNaN(millis) ? (text ?? "–") : formatDateTime(millis / 1000);
}

/** A compact duration: "850 ms", "12s", "4m12s", "2h05m", "3d04h". */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "–";
  const abs = Math.abs(seconds);
  if (abs < 1) return `${Math.round(abs * 1000)} ms`;
  const total = Math.round(abs);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days > 0) return `${days}d${pad(hours)}h`;
  if (hours > 0) return `${hours}h${pad(minutes)}m`;
  if (minutes > 0) return `${minutes}m${pad(secs)}s`;
  return `${secs}s`;
}

/** Latency between two timestamps in ms, or "–" when either is missing. */
export function formatLatency(from, to) {
  if (from === null || from === undefined || to === null || to === undefined) return "–";
  return formatDuration(to - from);
}

/** A count with a k/M suffix: 980, 1.2k, 3.4M. */
export function formatCount(value) {
  if (value === null || value === undefined) return "–";
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${trimZero((value / 1000).toFixed(1))}k`;
  return `${trimZero((value / 1_000_000).toFixed(1))}M`;
}

function trimZero(text) {
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

export function formatRate(perHour) {
  return `${formatCount(perHour)}/h`;
}

/** Watt-hours as kWh with up to three decimals: 7421 → "7.421 kWh". */
export function formatKwh(wattHours) {
  if (wattHours === null || wattHours === undefined || Number.isNaN(wattHours)) return "–";
  return `${trimTrailingZeros((wattHours / 1000).toFixed(3))} kWh`;
}

/** Watts as kW with up to two decimals: 7400 → "7.4 kW". */
export function formatKw(watts) {
  if (watts === null || watts === undefined || Number.isNaN(watts)) return "–";
  return `${trimTrailingZeros((watts / 1000).toFixed(2))} kW`;
}

function trimTrailingZeros(text) {
  return text.includes(".") ? text.replace(/\.?0+$/, "") : text;
}

/** OCPP sends many numbers as strings, often padded (" 1.2"). Returns a number when it is one. */
export function trimNumericString(value) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (trimmed === "" || !/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(trimmed)) return value;
  return Number(trimmed);
}
