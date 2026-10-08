# Use Case: Deliver Billing Messages to Secondary Backend

## Overview

**Use Case ID:** UC-006  
**Use Case Name:** Deliver Billing Messages to Secondary Backend  
**Primary Actor:** Secondary Backend  
**Goal:** The secondary backend receives every billing-relevant message in order, even after outages of the backend or the proxy.  
**Trigger:** A message is queued for the secondary backend, or the connection to the secondary backend is lost or restored.  
**Status:** Implemented  

## Preconditions

- A charger session exists (UC-002) and a secondary backend is configured.

## Main Success Scenario

1. System connects to the secondary backend.
2. System sends the charger's last known boot information until the secondary backend accepts it.
3. System sends the last known status of each connector.
4. System sends the queued messages one by one, oldest first, with their original timestamps.
5. System links the transaction numbers the secondary backend issues for started transactions to the primary's numbers.
6. System removes each delivered message from the queue and keeps waiting for new ones.

## Alternative Flows

### A1: Secondary backend unreachable

**Trigger:** The connection cannot be established or is lost (step 1)  
**Flow:**

1. System waits with increasing, randomized delays and tries again.
2. Use case continues at step 1.

### A2: Boot not accepted

**Trigger:** The secondary backend does not accept or answer the boot information (step 2)  
**Flow:**

1. System waits for the interval the backend names, or 60 seconds, and sends it again.
2. Use case continues at step 2.

### A3: Message not answered

**Trigger:** The secondary backend does not answer a message in time (step 4)  
**Flow:**

1. System retries the message; after the third unanswered attempt it drops the message and logs an error.
2. Use case continues at step 4.

### A4: Message rejected

**Trigger:** The secondary backend rejects a message (step 4)  
**Flow:**

1. System logs a warning and discards the message.
2. Use case continues at step 4.

### A5: Message cannot be translated

**Trigger:** A stop has no known secondary transaction (step 4)  
**Flow:**

1. System logs an error and discards the message.
2. Use case continues at step 4.

### A6: Queue full

**Trigger:** The queue holds more than the configured maximum of billing messages (step 4)  
**Flow:**

1. System drops the oldest meter reading, or the oldest message if none is left, and logs an error.
2. Use case continues at step 4.

## Postconditions

### Success Postconditions

- The secondary backend has received all queued billing messages that were deliverable.
- The queue and transaction links are stored on disk.

### Failure Postconditions

- The charging session is unaffected.
- Queued billing messages remain stored for the next attempt.

## Business Rules

### BR-001: Secondary never disturbs charging

Slowness, outages, or rejections of the secondary backend never affect the charger or the primary backend.

### BR-002: Durable messages

Transaction starts, transaction stops and meter readings are stored on disk and survive restarts of the proxy; a message taken from the queue is removed from disk. Heartbeats, card authorizations and status messages are only sent while connected and are not replayed; the boot and status information is resent from the last known values.

### BR-003: Order preserved

Messages are delivered in the order the charger sent them.

### BR-004: Retry delays

Reconnection waits start at 1 second, double up to 300 seconds, and are randomized by ±50%; the delay resets after a successful boot.

### BR-005: Attempt limit

A message is tried at most three times.

### BR-006: Queue limit

At most the configured number of durable messages are held (default 10,000); meter readings are dropped first.

### BR-007: Own message ids

Queued messages get new message ids because the charger's ids restart after a reboot.

### BR-008: State file protected

State is written atomically; an unreadable state file is kept aside for inspection rather than discarded, and the proxy starts with empty state.
