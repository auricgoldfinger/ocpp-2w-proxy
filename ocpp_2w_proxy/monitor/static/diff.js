// Pure structural diff of two JSON values, reported as changed paths ("meterValue[0].timestamp").

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function childPath(path, key) {
  if (typeof key === "number") return `${path}[${key}]`;
  return path ? `${path}.${key}` : key;
}

function walk(from, to, path, out) {
  if (Array.isArray(from) && Array.isArray(to)) {
    for (let at = 0; at < Math.max(from.length, to.length); at += 1) {
      compareChild(from, to, at, path, out);
    }
    return;
  }
  if (isObject(from) && isObject(to)) {
    const keys = new Set([...Object.keys(from), ...Object.keys(to)]);
    for (const key of keys) compareChild(from, to, key, path, out);
    return;
  }
  if (JSON.stringify(from) !== JSON.stringify(to)) out.push({ path: path || "(whole payload)", kind: "changed", from, to });
}

function compareChild(from, to, key, path, out) {
  const inFrom = Object.hasOwn(from, key);
  const inTo = Object.hasOwn(to, key);
  const at = childPath(path, key);
  if (inFrom && !inTo) out.push({ path: at, kind: "removed", from: from[key], to: undefined });
  else if (!inFrom && inTo) out.push({ path: at, kind: "added", from: undefined, to: to[key] });
  else walk(from[key], to[key], at, out);
}

/** Every path where `to` differs from `from`: [{path, kind: changed|added|removed, from, to}]. */
export function diffPaths(from, to) {
  const out = [];
  walk(from, to, "", out);
  return out;
}
