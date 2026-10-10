# Glossary

| Term              | Definition                                                                                                                      | Avoid                |
|-------------------|---------------------------------------------------------------------------------------------------------------------------------|----------------------|
| Charger           | The EV charging station that connects to the proxy and initiates status, meter and transaction messages.                        | Wallbox, EVSE        |
| Charger Owner     | The person responsible for a Charger and its connection to the proxy.                                                           |                      |
| Backend           | A central system (CSMS) the proxy connects to on behalf of a Charger.                                                           | Server, CSMS         |
| Backend Operator  | The person or organization responsible for operating a configured Backend.                                                       |                      |
| Primary Backend   | The one Backend that receives Charger messages, decides Card Authorization and issues transaction numbers. Its replies are returned to the Charger except that designated outage-queued updates receive an immediate local acknowledgement. A Primary Backend outage longer than the grace period takes the Charger offline. | Master, Main Backend |
| Primary Backend Operator | The person or organization responsible for operating the Primary Backend.                                                   |                      |
| Secondary Backend | Any configured Backend other than the Primary Backend; it receives the Charger messages configured for it and may only send permitted Commands (e.g. Home Assistant for solar charging, a statistics backend). There may be any number of them. | Slave, Second Backend |
| Proxy Administrator | The person who configures and runs the proxy.                                                                                 | Admin                |
| Secondary Backend Operator | The person or organization relying on a Secondary Backend to receive Charger information for billing, energy management or statistics. | |
| Command           | An OCPP request sent by a Backend to the Charger.                                                                               | Instruction          |
| Command Policy    | The per-Backend rule deciding whether a Command is forwarded, answered by the proxy, or refused.                                | Allowlist of commands |
| Allowlist         | The configured set of Charger identities that may connect.                                                                      | Whitelist            |
| Billing Message   | A Transaction start, Transaction stop or meter reading used for billing or statistics. It is relayed to the Primary Backend and stored durably for ordered delivery to each Secondary Backend configured to receive its type. | Billing event |
| Primary Outbox    | The per-Charger durable queue for `StopTransaction` and transaction `MeterValues` calls acknowledged locally during a Primary Backend outage, delivered in order. Beside it the proxy keeps only the newest connector, firmware and diagnostics status report per connector, sent once the queue is empty. | |
| Transaction       | A charging session, identified by a transaction number that differs per Backend; it is distinct from a Charger Connection. | |
| Charger Connection | The active connection through the proxy between a Charger and its Primary Backend; it can span multiple Transactions. | |
| Live Message      | A non-billing Charger message forwarded to a Secondary Backend only while connected, without durable replay. Boot information and connector status additionally retain their latest values for resending after reconnection. | |
| Exclusive Command | A Command that changes the Charger's behavior, settings or authorization (charging profiles, configuration, availability, reset, firmware, local authorization list, cache, reservations, vendor data transfer, remote start); at most one Backend may forward it. | Conflicting command |
| Authorization Command | An Exclusive Command that can let a card charge without the Primary Backend's Card Authorization: remote start, local authorization list, reservations, and changes to the Charger's authorization settings. Only the Primary Backend may forward it. | |
| Card Authorization | The Primary Backend's decision whether a charging card may charge, given in reply to the Charger's authorization or transaction start. | Card check |
| Charging Profile  | A Command-supplied power limit for a Transaction or connector; a limit of zero pauses charging without ending the Transaction. | Power schedule |
| Debug Endpoint    | An optional HTTP listener, disabled by default, that lets the Proxy Administrator send a Command directly to a connected Charger, bypassing every Command Policy, and returns the Charger's raw reply. | Admin API |
| Debug Command     | A Command sent through the Debug Endpoint; it is never forwarded to a Backend and its transaction numbers are the Charger's own. | |
