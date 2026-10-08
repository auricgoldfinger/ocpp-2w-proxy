# Use Case: Control Secondary Backend Commands

## Overview

**Use Case ID:** UC-005  
**Use Case Name:** Control Secondary Backend Commands  
**Primary Actor:** Secondary Backend  
**Secondary Actors:** Charger  
**Goal:** Superseded by UC-004 Relay Backend Commands, which covers commands of the Primary Backend and of any number of Secondary Backends.  
**Trigger:** Secondary Backend sends a command for the charger.  
**Status:** Obsolete  

## Preconditions

- See UC-004.

## Main Success Scenario

1. Secondary Backend sends a command.
2. System handles the command as described in UC-004.

## Alternative Flows

_None — see UC-004._

## Postconditions

### Success Postconditions

- See UC-004.

### Failure Postconditions

- See UC-004.

## Business Rules

### BR-001: Superseded

This use case is no longer maintained; UC-004 is the single specification for backend commands.
