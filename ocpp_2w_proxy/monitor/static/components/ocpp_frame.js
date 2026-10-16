// A detail frame rebuilt as the OCPP-J message array that went over the wire. A CallError's
// details arrive as the frame's payload (monitor/replay.py), so they go back in the last slot.

const BUILDERS = {
  call: (frame, action) => [2, frame.wire_id, action, frame.payload],
  result: (frame) => [3, frame.wire_id, frame.payload],
  error: (frame) => [4, frame.wire_id, frame.error?.code ?? "", frame.error?.description ?? "", frame.payload ?? {}],
};

/**
 * [2, id, action, payload], [3, id, payload] or [4, id, code, description, details]; a frame of
 * a type the UI does not know yet is shown as [type, id, payload] rather than failing the view.
 */
export function ocppArray(frame, action) {
  const build = BUILDERS[frame.msg_type] ?? ((unknown) => [unknown.msg_type, unknown.wire_id, unknown.payload]);
  return build(frame, action);
}
