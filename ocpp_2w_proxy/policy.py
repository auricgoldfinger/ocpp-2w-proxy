"""Per-backend policy deciding what happens to a command (Call) a backend sends to the charger.

Each action maps to a rule:
  forward - send it to the charger (possibly transformed)
  answer  - the proxy replies itself with a harmless canned CallResult
  error   - the proxy replies with a NotSupported CallError
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .ocpp import Call, CallError, CallResult, Reply


class Rule(StrEnum):
    FORWARD = "forward"
    ANSWER = "answer"
    ERROR = "error"


# Canned confirmations, valid per the OCPP 1.6 schemas, that tell a backend "not done"
# without making it retry forever.
CANNED_ANSWERS: Mapping[str, dict[str, Any]] = {
    "CancelReservation": {"status": "Rejected"},
    "ChangeAvailability": {"status": "Rejected"},
    "ChangeConfiguration": {"status": "Rejected"},
    "ClearCache": {"status": "Rejected"},
    "ClearChargingProfile": {"status": "Unknown"},
    "DataTransfer": {"status": "Rejected"},
    "GetCompositeSchedule": {"status": "Rejected"},
    "GetConfiguration": {"configurationKey": [], "unknownKey": []},
    "GetDiagnostics": {},
    # -1: local authorization list not enabled, so the backend won't try to push one.
    "GetLocalListVersion": {"listVersion": -1},
    "RemoteStartTransaction": {"status": "Rejected"},
    "RemoteStopTransaction": {"status": "Rejected"},
    "ReserveNow": {"status": "Rejected"},
    "Reset": {"status": "Rejected"},
    "SendLocalList": {"status": "NotSupported"},
    "SetChargingProfile": {"status": "Rejected"},
    "TriggerMessage": {"status": "Rejected"},
    "UnlockConnector": {"status": "NotSupported"},
    "UpdateFirmware": {},
}

# Every OCPP 1.6 action in either direction; configuration entries are validated
# against this so a misspelled action fails loudly instead of silently never matching.
OCPP_ACTIONS: frozenset[str] = frozenset(
    {
        # charger -> backend
        "Authorize",
        "BootNotification",
        "DataTransfer",
        "DiagnosticsStatusNotification",
        "FirmwareStatusNotification",
        "Heartbeat",
        "MeterValues",
        "StartTransaction",
        "StatusNotification",
        "StopTransaction",
        # backend -> charger
        "CancelReservation",
        "ChangeAvailability",
        "ChangeConfiguration",
        "ClearCache",
        "ClearChargingProfile",
        "GetCompositeSchedule",
        "GetConfiguration",
        "GetDiagnostics",
        "GetLocalListVersion",
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "ReserveNow",
        "Reset",
        "SendLocalList",
        "SetChargingProfile",
        "TriggerMessage",
        "UnlockConnector",
        "UpdateFirmware",
    }
)

# UC-001 BR-007: commands that change the charger's behavior, settings or authorization.
# Each may be forwarded by at most one backend.
EXCLUSIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "SetChargingProfile",
        "ClearChargingProfile",
        "ChangeConfiguration",
        "ChangeAvailability",
        "Reset",
        "ClearCache",
        "SendLocalList",
        "ReserveNow",
        "CancelReservation",
        "UpdateFirmware",
        "DataTransfer",
        "RemoteStartTransaction",
    }
)

# UC-001 BR-008: commands that can let a card charge without the primary backend's Card
# Authorization. Only the primary backend may forward them.
AUTHORIZATION_ACTIONS: frozenset[str] = frozenset(
    {
        "RemoteStartTransaction",
        "SendLocalList",
        "ReserveNow",
        "CancelReservation",
    }
)

# OCPP 1.6 configuration keys that change the charger's authorization behavior: the local
# authorization list, offline authorization, pre-authorization, authorization of remote
# starts and stopping on an invalid card, plus the charger's credentials and security profile.
# Only the primary backend may change them.
# Keys are lowercase: OCPP configuration keys are case-insensitive (CiString).
AUTHORIZATION_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "localauthlistenabled",
        "localauthorizeoffline",
        "localpreauthorize",
        "allowofflinetxforunknownid",
        "authorizationcacheenabled",
        "authorizeremotetxrequests",
        "stoptransactiononinvalidid",
        "maxenergyoninvalidid",
        # The charger's own credentials and transport security: changing them can lock it out.
        "authorizationkey",
        "securityprofile",
    }
)


# A secondary backend (e.g. a control or billing-mirror backend): may stop sessions and read
# state, but by default must not touch what the primary backend (which authorizes and bills)
# relies on: charging profiles, configuration, availability, firmware, the local
# authorization list, or card authorization (remote start). Config can hand it some of these.
SECONDARY_DEFAULT_RULES: Mapping[str, Rule] = {
    "RemoteStopTransaction": Rule.FORWARD,
    "TriggerMessage": Rule.FORWARD,
    "GetConfiguration": Rule.FORWARD,
    "UnlockConnector": Rule.FORWARD,
    "GetCompositeSchedule": Rule.FORWARD,
    "RemoteStartTransaction": Rule.ANSWER,
    "GetLocalListVersion": Rule.ANSWER,
    "SendLocalList": Rule.ANSWER,
    "ChangeConfiguration": Rule.ANSWER,
    "SetChargingProfile": Rule.ANSWER,
    "ClearChargingProfile": Rule.ANSWER,
    "ChangeAvailability": Rule.ANSWER,
    "Reset": Rule.ANSWER,
    "ClearCache": Rule.ANSWER,
    "ReserveNow": Rule.ANSWER,
    "CancelReservation": Rule.ANSWER,
    "DataTransfer": Rule.ANSWER,
    "UpdateFirmware": Rule.ANSWER,
    "GetDiagnostics": Rule.ANSWER,
}
SECONDARY_DEFAULT_RULE = Rule.ERROR

PRIMARY_DEFAULT_RULES: Mapping[str, Rule] = {}
PRIMARY_DEFAULT_RULE = Rule.FORWARD


@dataclass(frozen=True)
class CommandPolicy:
    rules: Mapping[str, Rule]
    default_rule: Rule
    # Lowercase OCPP configuration keys: CiString comparison is case-insensitive.
    change_configuration_allow_keys: frozenset[str] = frozenset()
    strip_charging_profile: bool = False
    # Lowercase keys owned by another backend: ChangeConfiguration for them is answered
    # "Rejected" here even though this policy otherwise forwards ChangeConfiguration.
    withheld_configuration_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for action, rule in self.rules.items():
            if rule is Rule.ANSWER and action not in CANNED_ANSWERS:
                raise ValueError(f"no canned answer exists for action {action!r}; use 'forward' or 'error'")
        if self.default_rule is Rule.ANSWER:
            raise ValueError("default rule cannot be 'answer'")

    def rule_for(self, action: str) -> Rule:
        return self.rules.get(action, self.default_rule)

    def effective_rule(self, call: Call) -> Rule:
        """The rule that applies to this call: policy rule, with per-key configuration overrides."""
        if call.action == "ChangeConfiguration":
            key = call.payload.get("key")
            if isinstance(key, str) and key.lower() in self.change_configuration_allow_keys:
                return Rule.FORWARD
            if isinstance(key, str) and key.lower() in self.withheld_configuration_keys:
                return Rule.ANSWER
        return self.rule_for(call.action)

    def decide(self, call: Call) -> Call | Reply:
        """Return the (possibly transformed) Call to forward, or the Reply the proxy sends back."""
        rule = self.effective_rule(call)

        match rule:
            case Rule.FORWARD:
                transform = _FORWARD_TRANSFORMS.get(call.action)
                return transform(self, call) if transform else call
            case Rule.ANSWER:
                return canned_answer(call)
            case Rule.ERROR:
                return CallError(call.id, "NotSupported", f"{call.action} is not allowed through this proxy")


def canned_answer(call: Call) -> CallResult:
    """The harmless confirmation the proxy sends for an action it does not forward."""
    return CallResult(call.id, dict(CANNED_ANSWERS[call.action]))


def _strip_charging_profile(policy: CommandPolicy, call: Call) -> Call:
    """A charging profile embedded in a remote start would override the control backend's schedule."""
    if not policy.strip_charging_profile or "chargingProfile" not in call.payload:
        return call
    payload = {k: v for k, v in call.payload.items() if k != "chargingProfile"}
    return call.with_payload(payload)


_FORWARD_TRANSFORMS: Mapping[str, Callable[[CommandPolicy, Call], Call]] = {
    "RemoteStartTransaction": _strip_charging_profile,
}
