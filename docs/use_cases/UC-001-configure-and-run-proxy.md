# Use Case: Configure and Run Proxy

## Overview

**Use Case ID:** UC-001  
**Use Case Name:** Configure and Run Proxy  
**Primary Actor:** Proxy Administrator  
**Goal:** The administrator starts the proxy with a valid configuration so that allowed chargers can connect.  
**Trigger:** Administrator starts the proxy with a configuration file.  
**Status:** Implemented  

## Preconditions

- A configuration file exists and is readable.
- Every secret the configuration refers to is available to the proxy as an environment variable.

## Main Success Scenario

1. Administrator starts the proxy and names the configuration file.
2. System reads and validates the configuration.
3. System sets up logging at the configured level.
4. System starts accepting charger connections on the configured address and port, secured with TLS when a certificate is configured.
5. Administrator later stops the proxy.
6. System stops accepting new connections and shuts down.

## Alternative Flows

### A1: Configuration invalid

**Trigger:** The configuration is unreadable, incomplete, or violates a rule (step 2)  
**Flow:**

1. System reports the configuration problem to the administrator.
2. Use case ends.

## Postconditions

### Success Postconditions

- The proxy accepts connections from allowlisted chargers.

### Failure Postconditions

- No charger can connect.

## Business Rules

### BR-001: Allowlist required

At least one charger must be configured; charger ids must be unique and consist of 1–64 letters, digits, dots, underscores or hyphens.

### BR-002: Primary backend required

A primary backend with a WebSocket address (ws:// or wss://) must be configured; the secondary backend is optional (without it the proxy is a one-way proxy).

### BR-003: Backend authentication modes

A backend authenticates with no credentials, with a password, or (primary only) by passing on the charger's own credentials. Passing charger credentials to the secondary backend is refused.

### BR-004: Passwords come from the environment

Passwords are never stored in the configuration file; a referenced environment variable that is missing or empty makes the configuration invalid.

### BR-005: TLS certificate and key together

A TLS certificate and its key must be configured together or not at all.

### BR-006: Command policy overrides

For each backend the administrator can override per command whether it is forwarded, answered by the proxy, or refused, and may whitelist configuration keys the secondary backend may change. A command can only be answered by the proxy if a standard answer exists for it.

### BR-007: Defaults

Unless configured otherwise the proxy listens on all addresses on port 8321, waits 30 seconds for a backend answer, queues at most 10,000 billing messages for the secondary backend, and does not log message contents.
