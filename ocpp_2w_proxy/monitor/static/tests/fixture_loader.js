import { readFileSync } from "node:fs";

/** Parses tests/fixtures/<name>.json; every call returns a fresh copy. */
export function loadFixture(name) {
  return JSON.parse(readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), "utf8"));
}

/** The exchange summary with this id from the exchanges fixture. */
export function exchangeById(id) {
  const found = loadFixture("exchanges").exchanges.find((exchange) => exchange.id === id);
  if (!found) throw new Error(`no fixture exchange ${id}`);
  return found;
}
