// Human labels for payload keys. OCPP 1.6 field names whose plain split reads badly are in the
// table (from the 1.6 JSON schemas); any other key is split at its camelCase humps.

const LABELS = {
  chargeBoxSerialNumber: "charge box serial",
  chargePointModel: "model",
  chargePointSerialNumber: "serial number",
  chargePointVendor: "vendor",
  chargingProfileId: "charging profile",
  configurationKey: "configuration keys",
  connectorId: "connector",
  csChargingProfiles: "charging profile",
  firmwareVersion: "firmware",
  iccid: "ICCID",
  id: "ID",
  idTag: "ID tag",
  idTagInfo: "ID tag info",
  imsi: "IMSI",
  messageId: "message ID",
  meterSerialNumber: "meter serial",
  meterStart: "meter at start",
  meterStop: "meter at stop",
  meterValue: "meter values",
  numberPhases: "phases",
  parentIdTag: "parent ID tag",
  readonly: "read-only",
  recurrencyKind: "recurrence",
  reservationId: "reservation",
  sampledValue: "sampled values",
  transactionId: "transaction",
  unknownKey: "unknown keys",
  vendorErrorCode: "vendor error",
  vendorId: "vendor",
};

// An all-capitals word ("URL", "ID") keeps its case; any other word is lower-cased.
function wordCase(word) {
  return word.length > 1 && word === word.toUpperCase() ? word : word.toLowerCase();
}

/** "vendorErrorCode" → "vendor error", "retrieveDate" → "retrieve date", "firmwareURL" → "firmware URL". */
export function fieldLabel(key) {
  if (Object.hasOwn(LABELS, key)) return LABELS[key];
  const words = String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .split(/[\s_]+/)
    .filter(Boolean);
  return words.length === 0 ? String(key) : words.map(wordCase).join(" ");
}
