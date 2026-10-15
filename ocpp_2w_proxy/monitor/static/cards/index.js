// Payload card per action, with the generic key/value card as the fallback.

import { genericCard } from "./generic.js";
import { meterValuesCard } from "./meter_values.js";
import { statusNotificationCard } from "./status_notification.js";
import { startCard, stopCard } from "./transaction.js";

const CARDS = {
  MeterValues: meterValuesCard,
  StatusNotification: statusNotificationCard,
  StartTransaction: startCard,
  StopTransaction: stopCard,
};

/** The card for `action`; `context` may carry the session aggregate and `now`. */
export function renderCard(action, payload, context = {}) {
  return (CARDS[action] ?? genericCard)(payload, context);
}

/** Actions whose card uses the session aggregate (GET /api/sessions/{tx}). */
export function wantsSession(action) {
  return action === "StartTransaction" || action === "StopTransaction";
}
