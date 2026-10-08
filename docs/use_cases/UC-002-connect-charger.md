# Use Case: Connect Charger

## Overview

**Use Case ID:** UC-002  
**Use Case Name:** Connect Charger  
**Primary Actor:** Charger  
**Secondary Actors:** Primary Backend  
**Goal:** The charger establishes a session through the proxy so that it is controlled by the primary backend.  
**Trigger:** Charger opens a connection to the proxy under its own id.  
**Status:** Implemented  

## Preconditions

- The proxy is running (UC-001).

## Main Success Scenario

1. Charger opens a connection that carries its id and, optionally, credentials.
2. System checks that the charger is on the allowlist and that any credentials are valid.
3. System ends an already running session of the same charger.
4. System connects to the primary backend on behalf of the charger.
5. System starts the session; messages are relayed as described in UC-003 and UC-004.
6. System connects to the secondary backend, if one is configured, and keeps retrying in the background (UC-006).
7. Session ends when the charger or the primary backend disconnects.

## Alternative Flows

### A1: Charger not allowed

**Trigger:** Charger id is missing, malformed, or not on the allowlist (step 2)  
**Flow:**

1. System refuses the connection as not found.
2. Use case ends.

### A2: Credentials invalid

**Trigger:** Credentials are malformed, name another charger, or the password is wrong or missing (step 2)  
**Flow:**

1. System refuses the connection as unauthorized.
2. Use case ends.

### A3: Primary backend unreachable

**Trigger:** The primary backend cannot be reached or credentials for it are missing (step 4)  
**Flow:**

1. System closes the charger connection and signals that the primary backend is unavailable.
2. Use case ends.

### A4: Primary backend disconnects

**Trigger:** The primary backend drops the connection during the session (step 7)  
**Flow:**

1. System ends the session and closes the charger connection.
2. Use case ends.

## Postconditions

### Success Postconditions

- Exactly one session exists for the charger.
- Every connection attempt is logged without revealing passwords.

### Failure Postconditions

- No session exists for the refused or failed connection.
- No password appears in the log.

## Business Rules

### BR-001: Allowlist

Only chargers listed in the configuration may connect.

### BR-002: Identity consistency

The id in the connection address and the username of any credentials must match.

### BR-003: Optional charger password

If a password is configured for the charger it is required and must match exactly; without a configured password only the id is checked, so the proxy should only be reachable from a trusted network.

### BR-004: One session per charger

A new connection of a charger replaces its previous session; the old session gets up to 15 seconds to finish.

### BR-005: Primary backend is mandatory

A session lives only as long as both the charger and the primary backend are connected. The secondary backend never ends a session.

### BR-006: Backend identities

The charger may be known under a different id at each backend; by default the charger id is used for both.

### BR-007: Credentials are not exposed

Passwords are never logged; the charger's credentials are never passed on to the secondary backend.
