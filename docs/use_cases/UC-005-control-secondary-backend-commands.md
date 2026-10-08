# Use Case: Control Secondary Backend Commands

## Overview

**Use Case ID:** UC-005  
**Use Case Name:** Control Secondary Backend Commands  
**Primary Actor:** Secondary Backend  
**Secondary Actors:** Charger  
**Goal:** The secondary (billing) backend can start and stop sessions and read the charger's state without being able to change anything the primary backend relies on.  
**Trigger:** Secondary backend sends a command for the charger.  
**Status:** Implemented  

## Preconditions

- A charger session exists (UC-002).
- The secondary backend is connected.

## Main Success Scenario

1. Secondary backend sends a command.
2. System checks the command against the secondary backend's command policy.
3. System translates transaction numbers in the command to the primary's numbering and removes any charging profile from a remote start.
4. System passes the command to the charger under a message id of its own.
5. Charger replies to the command (UC-003).
6. System returns the reply to the secondary backend under the original message id.

## Alternative Flows

### A1: Command answered by the proxy

**Trigger:** The policy answers this command type itself (step 2)  
**Flow:**

1. System replies to the secondary backend with a harmless standard answer such as "rejected", "not supported", or "no local list".
2. Use case ends.

### A2: Command type not permitted

**Trigger:** The command type is not covered by the policy (step 2)  
**Flow:**

1. System replies with a "not supported" error.
2. Use case ends.

### A3: Unknown transaction

**Trigger:** A remote stop names a transaction the proxy does not know (step 3)  
**Flow:**

1. System replies "rejected" to the secondary backend.
2. Use case ends.

### A4: Secondary backend offline

**Trigger:** The secondary backend disconnected before the reply is ready (step 6)  
**Flow:**

1. System logs a warning and drops the reply.
2. Use case ends.

## Postconditions

### Success Postconditions

- The charger has executed the permitted command and the secondary backend has its reply.

### Failure Postconditions

- The charger's configuration, charging profiles, availability, firmware and authorization list are unchanged by the secondary backend.

## Business Rules

### BR-001: Forwarded commands

By default remote start, remote stop, trigger message, get configuration, unlock connector and get composite schedule are passed to the charger.

### BR-002: Answered commands

By default get/send local list, change configuration, set and clear charging profile, change availability, reset, clear cache, reserve, cancel reservation, data transfer, firmware update and diagnostics are answered by the proxy with a harmless refusal and never reach the charger.

### BR-003: Configuration exceptions

A configuration change is passed on only for keys the administrator whitelisted.

### BR-004: Charging profile stripped

A charging profile attached to a remote start is removed because it would override the primary backend's solar charging.

### BR-005: Transaction translation

A remote stop uses the secondary's transaction number and is translated to the primary's number known to the charger.

### BR-006: Unlisted commands refused

Any command not in the policy is refused as not supported.
