# Use Case: Deliver Billing Messages to Secondary Backends

## Overview

**Use Case ID:** UC-006  
**Use Case Name:** Deliver Billing Messages to Secondary Backends  
**Primary Actor:** Secondary Backend Operator

**Secondary Actors:** Secondary Backend

**Goal:** The operator's Secondary Backend receives permitted Billing Messages in order after outages, without loss due to connection failures, subject only to queue overflow, forwarding-policy exclusions and the bounded handling of messages the backend keeps rejecting or that cannot be translated.

**Trigger:** A Billing Message becomes available for delivery, a retained Billing Message becomes eligible for retry, or a Secondary Backend connection needs recovery.

**Status:** Draft  

**Requirements:** [FR-010, FR-011, FR-012, FR-017, NFR-001, NFR-002, NFR-003, NFR-004, NFR-005, NFR-006, NFR-009, NFR-010, C-001, C-004](../requirements.md)

The proxy initiates delivery automatically for the operator; no operator action is needed per message. The queue is internal to the proxy, not an external actor. Billing Message delivery to the Primary Backend is specified in UC-003.

## Preconditions

- A Charger and its assigned Secondary Backend are configured, with exactly one Primary Backend assigned to that Charger (UC-001).
- The steps below run separately for each Secondary Backend assigned to the Charger, with its own connection, queue and transaction links.

## Main Success Scenario

1. System applies the Secondary Backend's forwarding policy to a new Billing Message and adds it to that backend's queue in Charger order; for a retry, System selects the oldest queued Billing Message and confirms it remains permitted.
2. System connects to the Secondary Backend.
3. System sends the Charger's last known boot information until the Secondary Backend accepts it. A boot the Charger sends while the backend is connected is handled the same way before any later message.
4. System sends the last known status of each connector.
5. System sends permitted queued Billing Messages one by one, oldest first, with their original timestamps and any required transaction-number translation. A backend that is not sent the Charger's heartbeats receives heartbeats from the System at the interval it named when accepting the boot.
6. System links the transaction numbers the Secondary Backend issues for started transactions to the Primary Backend's numbers.
7. System removes each successfully delivered message from that backend's queue after recording any required transaction link; undelivered messages remain stored.

## Alternative Flows

### A1: Secondary Backend unreachable

**Trigger:** The connection cannot be established or is lost (step 2)

**Flow:**

1. System waits with increasing, randomized delays and tries again.
2. Use case continues at step 2.

### A2: Boot not accepted

**Trigger:** The Secondary Backend does not accept or answer the boot information (step 3)

**Flow:**

1. System waits for the interval the backend names, or 60 seconds, and sends it again.
2. Use case continues at step 3.

### A3: Status not accepted

**Trigger:** The Secondary Backend does not accept or answer a connector status (step 4)

**Flow:**

1. System logs the timeout or rejection and retains the latest connector status for resending after reconnection.
2. Use case continues at step 5.

### A4: Message not answered

**Trigger:** The Secondary Backend does not answer a message in time (step 5)

**Flow:**

1. System logs the timeout, retains the message and closes the connection, because a silent connection may be broken. A timeout never counts as a rejection under A5.
2. System waits with increasing, randomized delays as defined in BR-004 before reconnecting; later Billing Messages for this backend remain queued.
3. Use case continues at step 2.

### A5: Message rejected

**Trigger:** The Secondary Backend rejects a message (step 5)

**Flow:**

1. System logs a warning and retains the rejected message; a backend rejection is not a forwarding-policy exclusion.
2. System waits with increasing, randomized delays as defined in BR-004 before retrying; later Billing Messages for this backend remain queued.
3. If the backend has now rejected the message 5 times in a row (BR-005), System discards it, logs an error naming the backend, the message type and the backend's reason, and for a transaction stop removes the transaction link.
4. Use case continues at step 5.

### A6: Message cannot be translated

**Trigger:** A Billing Message requiring transaction-number translation has no linked transaction of this Secondary Backend (step 5)

**Flow:**

1. For a transaction stop, System logs an error and discards the message: messages are delivered in order, so the start's answer would already have created the link, and it can no longer appear.
2. For meter readings, System sends them without a transaction number.
3. Use case continues at step 5.

### A7: Queue full

**Trigger:** Adding a new Billing Message would exceed the configured queue capacity (step 1)

**Flow:**

1. System drops the oldest meter reading, or the oldest queued message if no meter reading is queued, and logs the overflow.
2. System adds the new Billing Message to the queue.
3. Use case continues at step 2.

### A8: New message excluded by forwarding policy

**Trigger:** The Secondary Backend's forwarding policy excludes the new Billing Message's type (step 1)

**Flow:**

1. System does not add the message to this backend's queue. This is not a discard, and it is not logged, because excluded types are a normal part of the configuration.
2. Use case ends.

### A9: Queued message excluded by forwarding policy

**Trigger:** The Secondary Backend's forwarding policy excludes the oldest queued Billing Message's type (step 1)

**Flow:**

1. System discards the message for this backend and logs the forwarding-policy exclusion.
2. Use case continues at step 1.

### A10: Retained state cannot be recovered

**Trigger:** The retained state file of the Charger cannot be read, or a single retained Billing Message cannot be read (step 1)

**Flow:**

1. For an unreadable state file, System logs an error, keeps the file aside for recovery under a `.corrupt` name, and starts with empty state, so that new Billing Messages are still delivered.
2. For a single unreadable message, System logs an error and skips it.
3. Use case continues at step 1.

## Postconditions

### Success Postconditions

- The Secondary Backend has received its permitted Billing Messages in order, except messages discarded under BR-006 or BR-009.
- Its pending Billing Messages and transaction links remain available for subsequent deliveries.

### Failure Postconditions

- The charging session and the delivery to every other backend are unaffected.
- Undelivered Billing Messages remain stored for the next attempt unless discarded under BR-005, BR-006 or BR-009; timeouts never discard them.

## Business Rules

### BR-001: Secondary Backends never disturb charging or each other

Slowness, outages, or rejections of a Secondary Backend never affect the charger, the Primary Backend, or any other Secondary Backend.

### BR-002: Durable messages

Billing Messages are retained durably and survive restarts of the proxy. A message is removed only after successful delivery and recording any required transaction link, or under BR-005, BR-006 or BR-009. Live Message forwarding is specified in UC-003; boot and status information is resent from the last known values.

### BR-003: Order preserved

Retained Billing Messages are delivered to each Secondary Backend in the order the Charger sent them. An unanswered or rejected message prevents later Billing Messages for that backend from overtaking it until it is delivered or discarded under BR-005, without blocking the Charger, the Primary Backend or any other Secondary Backend.

### BR-004: Retry delays

Reconnection and message-retry waits start at 1 second, double up to 300 seconds, and are randomized by ±50%. Reconnection delay resets after a successful boot; message-retry delay resets only after successful message delivery. After a message timeout (A4), the wait before reconnecting is at least the message-retry delay, so a message that keeps timing out is retried less and less often.

### BR-005: Attempt limits

Timeouts and lost connections never limit delivery attempts. A message the Secondary Backend rejects is retried; after 5 rejections in a row (following OCPP 1.6 TransactionMessageAttempts) it is discarded, so it does not hold up every later message forever. The rejection count is kept in memory only, so a restart of the proxy starts it over. A transaction stop without a transaction link is discarded at once (A6).

### BR-006: Queue limit

Each Secondary Backend's durable queue has a default capacity of 10,000 Billing Messages, configurable to a capacity of at least 10,000. When adding a message would exceed the capacity, the oldest meter reading is dropped first; if there is no queued meter reading, the oldest queued message is dropped.

### BR-007: Own message ids

Queued messages get new message ids because the charger's ids restart after a reboot.

### BR-008: Retained delivery state protected

Retained delivery state is protected from interruption during updates. If the retained state cannot be read, the file is kept aside for recovery (`.corrupt`) and delivery continues from empty state, so billing data that arrives later is not lost as well (A10).

### BR-009: Forwarding-policy exclusion

An undelivered Billing Message may be discarded only because of queue overflow under BR-006, the attempt limits of BR-005, being unreadable (A10), or because its type is no longer in that backend's configured forwarding policy (A9). A backend rejecting a permitted message does not change that policy. Every discard is logged with the backend and reason.
