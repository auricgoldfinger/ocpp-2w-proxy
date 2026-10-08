# Use Case: Relay Backend Commands

## Overview

**Use Case ID:** UC-004  
**Use Case Name:** Relay Backend Commands  
**Primary Actor:** Primary Backend, Secondary Backend  
**Secondary Actors:** Charger  
**Goal:** A backend controls the charger within the role its command policy grants, as if it were connected directly, without being able to override another backend.  
**Trigger:** A backend sends a command for the charger.  
**Status:** Draft  

**Requirements:** [FR-005, FR-006, FR-007, FR-009, FR-012, FR-019, FR-020, NFR-001](../requirements.md)

## Preconditions

- A charger session exists (UC-002).
- The backend is connected.
- The configuration assigns every Exclusive Command to at most one backend and every Authorization Command only to the Primary Backend (UC-001).

## Main Success Scenario

1. Backend sends a command.
2. System checks the command against the sending backend's command policy.
3. System adapts the command for the charger: for a Secondary Backend it translates transaction numbers to the Primary Backend's numbering.
4. System passes the command to the charger under a message id of its own.
5. Charger replies to the command (UC-003).
6. System returns the reply to the sending backend only, under the original message id.

## Alternative Flows

### A1: Command answered by the proxy

**Trigger:** The policy answers this command type itself (step 2)  
**Flow:**

1. System replies to the backend with a harmless standard answer such as "rejected", "not supported", or "no local list".
2. Use case ends.

### A2: Command not permitted

**Trigger:** The policy refuses the command type or does not cover it (step 2)  
**Flow:**

1. System replies with a "not supported" error.
2. Use case ends.

### A3: Unknown transaction

**Trigger:** A remote stop from a Secondary Backend names a transaction the proxy cannot link to the Primary Backend's numbering (step 3)  
**Flow:**

1. System replies "rejected" to the Secondary Backend.
2. Use case ends.

### A4: Backend offline

**Trigger:** The sending backend disconnected before the reply is ready (step 6)  
**Flow:**

1. System logs a warning naming the backend and drops the reply.
2. Use case ends.

## Postconditions

### Success Postconditions

- The charger has executed the permitted command and the sending backend has its reply.
- No other backend has seen the reply.

### Failure Postconditions

- No command that the sending backend's policy does not forward reaches the charger.
- No Secondary Backend has started a charging session or changed the charger's authorization behavior.

## Business Rules

### BR-001: Policy per backend

Each backend has its own command policy that forwards, answers or refuses each command type. Unless configured otherwise the Primary Backend's commands are all forwarded, and a Secondary Backend's commands are forwarded only for remote stop, trigger message, get configuration, unlock connector and get composite schedule.

### BR-002: Answered commands

Commands a Secondary Backend may not forward are answered by default with a harmless refusal when a standard answer exists (get/send local list, change configuration, set and clear charging profile, change availability, reset, clear cache, reserve, cancel reservation, data transfer, remote start, firmware update, diagnostics); any other command is refused as not supported.

### BR-003: Exclusive Commands owned once

An Exclusive Command reaches the charger only from the one backend that is configured to forward it, so two backends can never send conflicting instructions.

### BR-004: Only the Primary Backend starts charging

Authorization Commands (remote start, local authorization list, reservations, authorization settings) are only forwarded for the Primary Backend. A Secondary Backend therefore cannot start a charging session for a card the Primary Backend denied, nor make the charger accept such a card locally.

### BR-005: Pause and resume with a Charging Profile

A Secondary Backend that owns the charging profile commands lowers, pauses (limit zero) and resumes the charging power of a running Transaction with a Charging Profile. The Transaction stays open and remains the one the Primary Backend authorized; stopping a Transaction ends it, and only the Primary Backend or the driver can start a new one.

### BR-006: Unique message ids

Each backend command is given a fresh message id towards the charger, so backends never receive each other's replies even when they reuse the same ids.

### BR-007: Transaction translation

A Secondary Backend uses its own transaction numbers; commands naming a transaction are translated to the Primary Backend's number known to the charger.

### BR-008: Charging profile stripped

A charging profile attached to a forwarded remote start is removed unless the sending backend owns the set charging profile command, because it would override that backend's Charging Profiles.
