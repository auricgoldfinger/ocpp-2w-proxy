// Which drawer view is showing. Every exchange opens in Readable; the choice is not stored and
// only lasts while the same exchange stays open (so live reloads keep it).

import { VIEWS } from "../components/view_tabs.js";

const MODES = VIEWS.map((view) => view.mode);
const DEFAULT_MODE = MODES[0];

export function createViewMode() {
  let exchangeId = null;
  let mode = DEFAULT_MODE;
  return {
    get mode() {
      return mode;
    },
    /** Opening a different exchange starts it in Readable. */
    open(id) {
      if (id === exchangeId) return;
      exchangeId = id;
      mode = DEFAULT_MODE;
    },
    /** Switches to `next`; returns whether the mode changed. */
    select(next) {
      if (!MODES.includes(next) || next === mode) return false;
      mode = next;
      return true;
    },
  };
}

/** The tab a key press moves to from `current` (arrows wrap; Home and End), or null. */
export function modeForKey(key, current) {
  const at = MODES.indexOf(current);
  const moves = {
    ArrowLeft: (at - 1 + MODES.length) % MODES.length,
    ArrowRight: (at + 1) % MODES.length,
    Home: 0,
    End: MODES.length - 1,
  };
  return key in moves ? MODES[moves[key]] : null;
}
