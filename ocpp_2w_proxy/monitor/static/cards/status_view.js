// Badge views for OCPP status enums. Each card names its own statuses; an unknown one gets a
// neutral "?" so a new firmware value still reads as text.

/** {label, icon, tone} of `status` from `views` ({Status: {icon, tone}}). */
export function statusView(status, views) {
  const label = typeof status === "string" && status.trim() ? status.trim() : "unknown";
  return { label, ...(views[label] ?? { icon: "?", tone: "unknown" }) };
}
