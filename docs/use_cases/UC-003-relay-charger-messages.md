# Use Case: Relay Charger Messages

## Overview

**Use Case ID:** UC-003  
**Use Case Name:** Relay Charger Messages  
**Primary Actor:** Charger  
**Secondary Actors:** Primary Backend, Secondary Backend  
**Goal:** Every message the charger sends reaches the primary backend, and the billing-relevant ones also reach the secondary backend, without the charger noticing the secondary.  
**Trigger:** Charger sends a message during a session.  
**Status:** Implemented  

## Preconditions

- A charger session exists (UC-002).

## Main Success Scenario

1. Charger sends a message such as a boot notification, status, card authorization, start or stop of a charging transaction, or meter readings.
2. System logs the message with card numbers masked.
3. System passes the message to the secondary backend's delivery queue if its type is configured for forwarding (UC-006).
4. System sends the message to the primary backend and waits for its answer.
5. System links the transaction numbers of both backends when a charging transaction starts.
6. System returns the primary backend's answer to the charger.

## Alternative Flows

### A1: Primary backend does not answer

**Trigger:** No answer arrives within the timeout or the primary backend is gone (step 4)  
**Flow:**

1. System logs the problem and gives the charger no answer.
2. Use case ends.

### A2: Malformed message

**Trigger:** The message cannot be understood (step 1)  
**Flow:**

1. System logs a warning and ignores the message.
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

- The primary backend has received the message and the charger has its answer.
- The secondary backend's delivery queue holds the message if it is configured for forwarding.
- When a transaction was started, both backends' transaction numbers are linked and stored.

### Failure Postconditions

- The charger receives no wrong or invented answer.
- Delivery to the secondary backend is not affected.

## Business Rules

### BR-001: Primary decides

Only the primary backend's answers are seen by the charger, including the authorization of cards. The secondary backend's answers are kept by the proxy and never passed to the charger.

### BR-002: Configurable forwarding

Boot, heartbeat, status, authorization, start, stop and meter reading messages are forwarded to the secondary backend by default; the list is configurable. Vendor-specific data transfers and firmware or diagnostics notifications go to the primary backend only unless listed.

### BR-003: Transaction number mapping

Each backend issues its own transaction number; the charger only knows the primary's. The proxy stores the pairing so that later messages to the secondary backend carry the secondary's number.

### BR-004: Card authorization warning

If the secondary backend rejects a card the primary accepted, a warning is logged because the session may not be billed.

### BR-005: Concurrent relaying

Messages are handled concurrently so that charger replies to backend commands are never blocked by a waiting call.

### BR-006: Masked logging

Card numbers are masked except the last four characters; message contents are only logged when debug logging and payload logging are both enabled.
