# Use Case: Relay Primary Backend Commands

## Overview

**Use Case ID:** UC-004  
**Use Case Name:** Relay Primary Backend Commands  
**Primary Actor:** Primary Backend  
**Secondary Actors:** Charger  
**Goal:** The primary backend controls the charger, for example to charge on solar surplus, as if it were connected directly.  
**Trigger:** Primary backend sends a command for the charger.  
**Status:** Implemented  

## Preconditions

- A charger session exists (UC-002).

## Main Success Scenario

1. Primary backend sends a command.
2. System checks the command against the primary backend's command policy.
3. System passes the command to the charger under a message id of its own.
4. Charger replies to the command (UC-003).
5. System returns the reply to the primary backend under the original message id.

## Alternative Flows

### A1: Command answered or refused by policy

**Trigger:** The policy for this command type is "answer" or "refuse" (step 2)  
**Flow:**

1. System replies to the primary backend itself, with a standard answer or a "not supported" error.
2. Use case ends.

## Postconditions

### Success Postconditions

- The charger has received the command and the primary backend its reply.

### Failure Postconditions

- No command reaches the charger when policy answered or refused it.

## Business Rules

### BR-001: Everything allowed by default

All commands of the primary backend are passed to the charger unless the administrator configures otherwise.

### BR-002: Unique message ids

Each backend command is given a fresh message id towards the charger, so that the two backends can never receive each other's replies even when they reuse the same ids.
