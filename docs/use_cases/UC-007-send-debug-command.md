# Use Case: Send Debug Command

## Overview

**Use Case ID:** UC-007  
**Use Case Name:** Send Debug Command  
**Primary Actor:** Proxy Administrator  
**Secondary Actors:** Charger  
**Goal:** The Proxy Administrator sends any Charger-bound OCPP command to a connected Charger and sees the Charger's raw answer, to find out how the Charger really behaves.  
**Trigger:** The Proxy Administrator sends a request to the Debug Endpoint.  
**Status:** Draft  

**Requirements:** [FR-022, NFR-012](../requirements.md)

## Preconditions

- The Debug Endpoint is enabled in the configuration (UC-001); it is off by default.
- A charger session exists for the named Charger (UC-002).

## Main Success Scenario

1. Proxy Administrator sends a command (action, payload, optional timeout) for a Charger to the Debug Endpoint.
2. System checks that the request is not from a browser, is JSON, and that the action is a Charger-bound OCPP 1.6 action.
3. System checks that no other debug command is in flight for that Charger.
4. System passes the command to the Charger under a message id of its own, without applying any Command Policy or transaction number translation.
5. Charger replies (result or error).
6. System returns the reply to the Proxy Administrator together with the frames sent and received and the latency, and logs one audit line.

## Alternative Flows

### A1: Invalid request

**Trigger:** The request is malformed, too large, has an unknown or Charger-originated action, or an invalid payload object (step 2)  
**Flow:**

1. System answers with an error status (400, or a parser limit status) and nothing is sent to the Charger.
2. Use case ends.

### A2: Browser request

**Trigger:** The request carries an `Origin` header or is not `application/json` (step 2)  
**Flow:**

1. System answers 403 and nothing is sent to the Charger.
2. Use case ends.

### A3: Charger not connected

**Trigger:** The Charger is configured but offline, or is not configured (step 3)  
**Flow:**

1. System answers 404; the command is not queued.
2. Use case ends.

### A4: Command already in flight

**Trigger:** Another debug command is waiting for this Charger's reply (step 3)  
**Flow:**

1. System answers 409.
2. Use case ends.

### A5: Charger does not answer

**Trigger:** No reply within the timeout (step 5)  
**Flow:**

1. System answers 504 with the frame that was sent; the charger session stays up.
2. Use case ends.

### A6: Charger disconnects

**Trigger:** The Charger connection closes before the reply (step 5)  
**Flow:**

1. System answers 502.
2. Use case ends.

## Postconditions

### Success Postconditions

- The Proxy Administrator has the Charger's own answer, including an error answer, and the exact frames exchanged.
- One audit log line records the command.

### Failure Postconditions

- No command reached the Charger when the request was refused.
- No Backend has seen the command or its reply.

## Business Rules

### BR-001: Off by default and local

The Debug Endpoint does not listen unless enabled and by default binds to the loopback address only. It has no authentication; a warning is logged when it is bound to another address.

### BR-002: No browser access

Requests with an `Origin` header, or without `Content-Type: application/json`, are refused, and only POST can send a command.

### BR-003: One command per Charger

At most one debug command is in flight per Charger.

### BR-004: Policy and translation bypassed

A Debug Command ignores Command Policies and Exclusive Command ownership, and transaction numbers are not translated, so the Charger's own transaction number must be used.

### BR-005: Never forwarded to Backends

Neither the Debug Command nor its reply is shown to any Backend, but its effects on the Charger (configuration, availability, charging profiles, firmware) persist and can interfere with the commands of a Backend.

### BR-006: Charger answer is verbatim

The system reports what the Charger answered, marked as answered by the Charger, never a standard answer of the proxy.
