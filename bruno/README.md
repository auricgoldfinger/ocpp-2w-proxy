# Bruno collection: OCPP 2W proxy debug endpoint

Requests for the proxy's debug endpoint, which sends raw OCPP 1.6 CALLs to the connected charger.

## Open it

1. Enable `[debug]` in the proxy config (it is off by default) and start the proxy.
2. In Bruno: Open Collection and pick this `bruno/` folder.
3. Select the `local` environment (top right).

## Environment variables (`environments/local.bru`)

| Variable    | Default                 | Meaning                                  |
|-------------|-------------------------|------------------------------------------|
| `baseUrl`   | `http://127.0.0.1:8322` | Debug listener of the proxy              |
| `chargerId` | `99224881`              | Charger to send commands to              |
| `timeout`   | `30`                    | Seconds to wait for the charger's answer |

## Layout

- `chargers/list-chargers.bru`: `GET /debug/chargers`.
- `commands/`: one request per charger-bound OCPP 1.6 action (SetChargingProfile has a TxDefaultProfile and a TxProfile variant). Each has a docs tab with side effects and, for dangerous actions, a hardware-risk note.
- `diagnostics/`: the SetChargingProfile troubleshooting playbook, numbered in the order to run it.

Debug commands are not translated: use the charger's own transaction id (the examples use 10048).

## Important: Accepted means stored, not applied

A charger answering `Accepted` to SetChargingProfile has only stored the profile. Whether it is applied must be verified with GetCompositeSchedule and `Current.Offered` in the MeterValues (proxy log). HTTP 200 from the debug endpoint only means the proxy got an answer from the charger; read the `status`/`result` in the body.

## Playbook (folder `diagnostics/`)

1. GetConfiguration: SupportedFeatureProfiles, ChargingScheduleMaxPeriods, MaxChargingProfilesInstalled, ConnectorSwitch3to1PhaseSupported (and unit/stack-level keys).
2. TriggerMessage MeterValues: baseline `Current.Offered`.
3. GetCompositeSchedule: connector 1 in A, connector 1 in W, connector 0.
4. ClearChargingProfile (with filters; an empty payload clears everything).
5. SetChargingProfile variants: TxDefault absolute, TxProfile relative, W unit, numberPhases 1, Absolute with startSchedule (edit the time first), ChargePointMaxProfile, then the stack-level sweep.
6. After each variant, repeat TriggerMessage MeterValues and GetCompositeSchedule and compare the schedule with `Current.Offered`. Each request's docs tab says what to compare.

## Safety

No authentication; the endpoint can damage hardware (UpdateFirmware, Reset Hard, UnlockConnector, ChangeConfiguration, empty ClearChargingProfile, DataTransfer, GetDiagnostics). Keep it bound to loopback.
