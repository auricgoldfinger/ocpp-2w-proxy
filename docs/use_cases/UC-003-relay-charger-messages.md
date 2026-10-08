# Use Case: Relay Charger Messages

## Overview

**Use Case ID:** UC-003  
**Use Case Name:** Relay Charger Messages  
**Primary Actor:** Charger  
**Secondary Actors:** Primary Backend, Secondary Backend  
**Goal:** Every message the charger sends reaches the Primary Backend, and the messages configured for each Secondary Backend also reach that backend, without the charger noticing any Secondary Backend.  
**Trigger:** Charger sends a message during a session.  
**Status:** Draft  

**Requirements:** [FR-003, FR-004, FR-006, FR-012, FR-017, FR-021, NFR-001, NFR-002, NFR-003, NFR-009, NFR-011](../requirements.md)

## Preconditions

- A charger session exists (UC-002).

## Main Success Scenario

1. Charger sends a message such as a boot notification, status, card authorization, start or stop of a charging transaction, or meter readings.
2. System logs the message with card numbers masked.
3. System hands the message to each Secondary Backend configured to receive its type: Billing Messages are retained for durable delivery (UC-006); Live Messages are forwarded only while that backend is connected, with the latest boot information and connector status retained for reconnection. A `StartTransaction` is excluded here: it is handed over only in step 5, after the Primary Backend confirmed the transaction.
4. If the Primary Backend is connected, System sends the message and waits for its answer
   without waiting for Secondary Backend delivery. If the Primary Backend is unavailable,
   System queues eligible messages for later delivery and handles other calls as described
   in A1.
5. System records the Primary Backend's transaction number when a charging transaction starts, then hands the confirmed start to each configured Secondary Backend, so that each backend's own number can be linked to it - also when that backend answers much later.
6. System returns the Primary Backend's answer, including its Card Authorization, to the
   Charger; for an eligible queued message, System immediately returns an empty local OCPP
   acknowledgement.

## Alternative Flows

### A1: Primary Backend does not answer

**Trigger:** No answer arrives within the timeout or the Primary Backend is gone (step 4)  
**Flow:**

1. If the message is a `StatusNotification`, `MeterValues`, or `StopTransaction`, System
   stores it durably in the Primary outbox and returns an empty OCPP `CallResult` to the
   Charger; the Primary Backend's eventual reply is discarded. The same applies while the
   outbox still drains after a reconnection: those messages keep their place in line, so the
   Primary Backend never sees a newer message before an older queued one.
2. Otherwise, System returns an OCPP `GenericError`; it does not queue or replay calls whose
   Primary Backend reply is needed, such as `Authorize`, `StartTransaction`, `BootNotification`,
   or `DataTransfer`. A `Heartbeat` is answered locally with the current time, so the Charger
   keeps its clock in sync without treating the outage as a fault.
3. System keeps the established Charger session open and reconnects to the Primary Backend
   with exponential backoff and jitter; queued messages are sent oldest first.
4. Use case ends when the Charger disconnects.

### A2: Malformed message

**Trigger:** The message cannot be understood (step 1)  
**Flow:**

1. System logs a warning and answers with a `ProtocolError` `CallError` on the message id it
   could still read off the frame (a fresh id otherwise), so the sender does not wait for an
   answer it will never get.
2. Use case ends.

### A3: Answer to a backend command

**Trigger:** The message is the charger's reply to a command from a backend (step 1)  
**Flow:**

1. System hands the reply to the backend that sent the command, with the original message id.
2. Use case ends.

### A4: Reply to unknown command

**Trigger:** The reply matches no outstanding command (step 1)  
**Flow:**

1. System logs a warning and ignores the reply.
2. Use case ends.

## Postconditions

### Success Postconditions

- The Primary Backend has received the message and the Charger has its answer, or the
  message remains durably queued under A1.
- Each configured Secondary Backend's Billing Message has been delivered or remains durably queued, unless discarded under UC-006 BR-006 or BR-009.
- Live Messages have been forwarded to connected, configured Secondary Backends; the latest boot information and connector status are retained for reconnection.
- When a transaction was started, the Primary Backend's transaction number is stored for linking.

### Failure Postconditions

- The Charger receives no invented Card Authorization or transaction id.
- A start the Primary Backend did not confirm reaches no Secondary Backend, so no unconfirmed
  charging session accumulates in any Secondary Backend (step 3).
- A local acknowledgement under A1 confirms receipt into the proxy's queue, not acceptance
  by the Primary Backend.
- Delivery to the Secondary Backends is not affected.

## Business Rules

### BR-001: Primary decides

Only the Primary Backend decides authorization and issues transaction ids. Its answers are
returned to the Charger; the proxy's empty acknowledgement for an eligible queued message
only confirms that the proxy stored the message and cannot authorize charging. Secondary
Backend answers are never passed to the Charger.

### BR-002: Configurable forwarding

The message types each Secondary Backend receives are configured per backend; by default boot, heartbeat, status, authorization, start, stop and meter reading messages. Vendor-specific data transfers and firmware or diagnostics notifications go to the Primary Backend only unless listed.

### BR-003: Transaction number mapping

Each backend issues its own transaction number; the charger only knows the Primary Backend's. The proxy stores a pairing per Secondary Backend so that later messages to that backend carry its own number.

### BR-004: Card authorization warning

If a Secondary Backend rejects a card the Primary Backend accepted, a warning naming that backend is logged; the charging session continues.

### BR-005: Concurrent relaying

Messages are handled concurrently so that charger replies to backend commands are never blocked by a waiting call.

### BR-006: Masked logging

Card numbers are masked except the last four characters; message contents are only logged when debug logging and payload logging are both enabled.

### BR-007: Primary Billing Message delivery

Transaction starts, Transaction stops and meter readings are Billing Messages. A
`StopTransaction` and `MeterValues` message received while the Primary Backend is unavailable
are stored durably and replayed in order; a `StartTransaction` is not queued because its
Primary Backend reply supplies the transaction id. A `StatusNotification`, `MeterValues` or
`StopTransaction` sent while the outbox drains is stored too and keeps its place in line
behind the older queued messages. On every new Primary Backend connection the proxy replays
the Charger's last `BootNotification`, so backends that key their charger state to the
booting connection accept the session's calls. UC-006 provides separate durable delivery
to Secondary Backends.

### BR-008: Primary outbox

The Primary outbox holds at most `max_queue` messages per Charger (default 10,000) across
proxy restarts. On overflow, queued meter readings are dropped first; if there is none, the
oldest message is dropped, and an error is logged. The message currently being sent is never
dropped by an overflow. Retries continue indefinitely
with delays that start at 1 second, double up to 300 seconds, and vary by ±50%. The outbox
continues draining after the Charger disconnects. When Primary Backend authentication
forwards Charger credentials, those credentials are never persisted; after a proxy restart,
delivery waits for the next authenticated Charger handshake.

A timed-out or interrupted delivery may be retried even if the Primary Backend received the
original call but its reply was lost; delivery is at least once, not exactly once.
