// Wires the filter chip bar to the URL hash: control changes become new filters, and new
// filters (from the hash) are written back into the controls.

import { DEFAULT_FILTERS, activeFilterCount } from "../filters.js";
import { actionOptions, heartbeatLabel } from "../components/filter_bar.js";
import { setHtml } from "../html.js";

const SEARCH_DELAY_MS = 300;

function control(form, name) {
  return form.elements.namedItem(name);
}

/**
 * Calls `onChange(filters)` when a control changes and `onFreeze()` for the ⏸ button.
 * `currentFilters()` returns the filters the controls start from.
 */
export function bindFilterBar(form, { currentFilters, onChange, onFreeze }) {
  const change = (patch) => onChange({ ...currentFilters(), ...patch });
  let searchTimer = null;

  form.addEventListener("submit", (event) => event.preventDefault());
  for (const name of ["peer", "outcome", "range"]) {
    control(form, name).addEventListener("change", (event) => change({ [name]: event.target.value }));
  }
  control(form, "action").addEventListener("change", (event) => change({ action: event.target.value.trim() }));
  control(form, "q").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => change({ q: event.target.value.trim() }), SEARCH_DELAY_MS);
  });
  control(form, "heartbeats").addEventListener("click", () => change({ heartbeats: !currentFilters().heartbeats }));
  control(form, "problem").addEventListener("click", () => change({ problem: !currentFilters().problem }));
  control(form, "clear").addEventListener("click", () => onChange({ ...DEFAULT_FILTERS, charger: currentFilters().charger }));
  control(form, "freeze").addEventListener("click", onFreeze);
}

function setValue(input, value) {
  if (input !== document.activeElement && input.value !== value) input.value = value;
}

/** Writes filters and list state into the controls. */
export function syncFilterBar(form, filters, { hidden, frozen, actions }) {
  for (const name of ["peer", "outcome", "range", "action", "q"]) setValue(control(form, name), filters[name]);
  const heartbeats = control(form, "heartbeats");
  heartbeats.setAttribute("aria-pressed", String(filters.heartbeats));
  heartbeats.textContent = heartbeatLabel(filters.heartbeats, hidden);
  control(form, "problem").setAttribute("aria-pressed", String(filters.problem));
  const freeze = control(form, "freeze");
  freeze.setAttribute("aria-pressed", String(frozen));
  freeze.textContent = frozen ? "▶ Resume" : "⏸ Freeze";
  control(form, "clear").hidden = activeFilterCount({ ...filters, charger: "" }) === 0;
  setHtml(form.querySelector("#action-list"), actionOptions(actions));
}
