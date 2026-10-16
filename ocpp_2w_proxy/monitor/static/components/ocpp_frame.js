// A detail frame rebuilt as the OCPP-J message array that went over the wire. A CallError's
// details arrive as the frame's payload (monitor/replay.py), so they go back in the last slot.

const BUILDERS = {
  call: (frame, action) => [2, frame.wire_id, action, frame.payload],
  result: (frame) => [3, frame.wire_id, frame.payload],
  error: (frame) => [4, frame.wire_id, frame.error?.code ?? "", frame.error?.description ?? "", frame.payload ?? {}],
};

/** [2, id, action, payload], [3, id, payload] or [4, id, code, description, details]. */
export function ocppArray(frame, action) {
  return BUILDERS[frame.msg_type](frame, action);
}
