# Use Case: Monitor Message Flow

## Overview

**Use Case ID:** UC-008  
**Use Case Name:** Monitor Message Flow  
**Primary Actor:** Proxy Administrator  
**Secondary Actors:** None  
**Goal:** The Proxy Administrator sees how each message travelled between the Charger and the Backends, and why a message did not reach a peer, without slowing down relaying.  
**Trigger:** The Proxy Administrator opens the Monitor in a browser.  
**Status:** Draft  

**Requirements:** [FR-023, FR-024, FR-025, FR-026, FR-027, FR-028, NFR-013, NFR-014, NFR-015, NFR-016, C-005](../requirements.md)

## Preconditions

- The Monitor is enabled in the configuration (UC-001); it is off by default.
- The Proxy Administrator's device can reach the Monitor's listen address.

## Main Success Scenario

1. Proxy Administrator opens the Monitor.
2. System shows the Charger, the proxy and each configured Backend with the state of their connections, and the most recent Exchanges of the selected Charger, newest first, with Heartbeats hidden behind a counter.
3. For each Exchange, System shows the action, a one-line headline and one lane per peer with the Leg's outcome: delivered, blocked or refused by the proxy, failed, queued, withheld, dropped, or not sent because that Backend is not configured for the action.
4. While the Proxy Administrator follows the list, System adds new Exchanges as the Charger and Backends exchange messages (UC-003, UC-004, UC-006, UC-007).
5. Proxy Administrator selects an Exchange.
6. System shows its detail in a Readable view: a sequence of the frames per Leg, a readable payload card of the request, and per Leg what its peer received (the differences with the original message), what it answered, its wire id, latency, outcome and the Decisions in plain words, then what the requester got back. The user can switch to a JSON view with every frame as the OCPP-J message it was, and the full record.
7. Proxy Administrator filters the list by Backend, action, outcome, problems, Transaction, card or message id, or loads older Exchanges.
8. System shows the matching Exchanges from its history.

## Alternative Flows

### A1: Monitor could not keep up

**Trigger:** Flow Events arrived faster than the Monitor could record them (step 3 or 4)  
**Flow:**

1. System drops the Flow Events it cannot record; relaying continues unchanged.
2. System shows a Monitor Gap row with the period and the number of lost events, and marks the Exchanges open during it as incomplete.
3. Use case continues at step 4.

### A2: Live connection lost

**Trigger:** The browser loses the live update stream, or falls too far behind (step 4)  
**Flow:**

1. System shows a banner and keeps the Exchanges already shown.
2. The browser reconnects and reloads the newest Exchanges.
3. Use case continues at step 4.

### A3: Follow paused

**Trigger:** The Proxy Administrator scrolls away from the newest Exchange or pauses the list (step 4)  
**Flow:**

1. System stops moving the list and shows the number of new Exchanges.
2. Proxy Administrator resumes following; System shows the new Exchanges.
3. Use case continues at step 4.

### A4: Modifying request

**Trigger:** A request uses a method other than GET, or its Origin differs from its Host (any step)  
**Flow:**

1. System refuses the request (405 or 403) and changes nothing.
2. Use case ends.

### A5: Monitor database unavailable

**Trigger:** The Monitor database cannot be opened at startup (precondition)  
**Flow:**

1. System logs an error and runs without the Monitor; relaying is unaffected.
2. Use case ends.

## Postconditions

### Success Postconditions

- The Proxy Administrator knows for each Leg of an Exchange whether it was delivered and, if not, why.
- No message, connection or Backend was changed by using the Monitor.

### Failure Postconditions

- Relaying between the Charger and the Backends was never delayed by the Monitor.

## Business Rules

### BR-001: Relaying first

The Monitor never delays relaying. When it cannot keep up it loses Flow Events and shows a Monitor Gap instead.

### BR-002: Read-only and masked

The Monitor only reads. Card numbers, AuthorizationKey values, credentials in URLs, SIM identities and DataTransfer data are masked before they are stored; a message whose action is unknown is masked in full.

### BR-003: Optional and local by default

The Monitor does not listen unless enabled and by default binds to the loopback address only. It has no authentication; a warning is logged when it is bound to another address.

### BR-004: Not sent is only for Charger messages

A Backend is shown as "not sent" only for a Charger message whose action that Backend is not configured to receive. The list is fixed when the Exchange is first recorded.

### BR-005: Problems

An Exchange is a problem when any Leg failed, or was dropped for any reason other than being replaced by a newer report. Messages blocked or refused by a Command Policy, and incomplete Exchanges, are not problems.

### BR-006: Retention

Exchanges are kept for the retention period (default 90 days) and then purged; there is no size limit.

### BR-007: Never colour alone

Every outcome and connection state is shown with an icon or line style and a word, not by colour only.

### BR-008: Why a Backend connection went down

Every Backend connection that goes down is recorded with who closed it and why (the Backend's close code, the proxy's own cause such as an unanswered call or no session, or a drop without a close frame), how long it had been up and how long since its last message. A Primary Backend connection that comes up records whether the Charger's boot was replayed or skipped. The Links view lists the outages of the last 24 hours, newest first, with these reasons.
