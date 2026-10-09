# Use Case: Configure and Run Proxy

## Overview

**Use Case ID:** UC-001  
**Use Case Name:** Configure and Run Proxy  
**Primary Actor:** Proxy Administrator  
**Goal:** The administrator starts the proxy with a valid configuration so that allowed chargers can connect to their backends.  
**Trigger:** Administrator starts the proxy with a configuration file.  
**Status:** Implemented  

**Requirements:** [FR-007, FR-008, FR-013, FR-014, FR-015, FR-016, FR-018, FR-019, NFR-002, NFR-007, C-004](../requirements.md)

## Preconditions

- A configuration file exists and is readable.
- Every secret the configuration refers to is available to the proxy as an environment variable.

## Main Success Scenario

1. Administrator starts the proxy and names the configuration file.
2. System reads and validates the configuration, including the backends and their command policies.
3. System checks that every Exclusive Command is forwarded by at most one backend and every Authorization Command only by the Primary Backend.
4. System sets up logging at the configured level.
5. System starts accepting charger connections on the configured address and port, secured with TLS when a certificate is configured.

## Alternative Flows

### A1: Configuration invalid

**Trigger:** The configuration is unreadable, incomplete, or violates a rule (step 2)  
**Flow:**

1. System reports the configuration problem to the administrator and stops.
2. Use case ends.

### A2: Conflicting Exclusive Command

**Trigger:** More than one backend is configured to forward the same Exclusive Command (step 3)  
**Flow:**

1. System reports an error naming the command and every backend that forwards it, and stops.
2. Use case ends.

### A3: Authorization Command on a Secondary Backend

**Trigger:** A Secondary Backend is configured to forward an Authorization Command (step 3)  
**Flow:**

1. System reports an error naming the command and the Secondary Backend, and stops.
2. Use case ends.

## Postconditions

### Success Postconditions

- The proxy accepts connections from allowlisted chargers.
- Each Exclusive Command is forwarded by at most one backend.

### Failure Postconditions

- No charger can connect.
- No backend is contacted.

## Business Rules

### BR-001: Allowlist required

At least one charger must be configured; charger ids must be unique and consist of 1–64 letters, digits, dots, underscores or hyphens.

### BR-002: Backends

Exactly one Primary Backend and zero or more Secondary Backends are configured, each with a WebSocket address (ws:// or wss://) and a name that is unique and used in logs and errors. The name `primary` is reserved for the Primary Backend. Without a Secondary Backend the proxy is a one-way proxy.

### BR-003: Backend authentication modes

A backend authenticates with no credentials, with a password, or (Primary Backend only) by passing on the charger's own credentials. Passing charger credentials to a Secondary Backend is refused.

### BR-004: Passwords come from the environment

Passwords are never stored in the configuration file; a referenced environment variable that is missing or empty makes the configuration invalid.

### BR-005: TLS certificate and key together

A TLS certificate and its key must be configured together or not at all.

### BR-006: Command policy overrides

For each backend the administrator can override per command whether it is forwarded, answered by the proxy, or refused. A command can only be answered by the proxy if a standard answer exists for it.

### BR-007: Exclusive Commands

The Exclusive Commands are: set and clear charging profile, change configuration, change availability, reset, clear cache, send local list, reserve, cancel reservation, update firmware, data transfer, and remote start. Each may be forwarded by at most one backend. Configuration changes may instead be permitted per configuration key; each key may then be permitted for at most one backend. A Secondary Backend may own a key while the Primary Backend forwards all configuration changes: the proxy then answers the Primary Backend's changes to that key with Rejected, so only one backend can change it.

### BR-008: Authorization Commands stay with the Primary Backend

Remote start, send local list, reserve, cancel reservation, and changes to the charger's authorization settings (local authorization, offline authorization, authorization of remote starts, stop on invalid card) may only be forwarded for the Primary Backend.

### BR-009: Defaults

Unless configured otherwise the proxy listens on all addresses on port 8321, waits 30 seconds for a backend answer, queues at most 10,000 Billing Messages for the Primary Backend and for each Secondary Backend (configurable, but never below 10,000), and does not log message contents. The Primary Backend forwards every command; a Secondary Backend forwards only remote stop, trigger message, get configuration, unlock connector and get composite schedule, and answers or refuses the rest. To give an Exclusive Command to a Secondary Backend, the administrator must also take it away from the Primary Backend.

### BR-010: Continuous operation

After successful startup the proxy runs continuously. Shutdown for failure, maintenance or updates is outside this startup use case, not part of its normal success scenario.
