"""Pydantic models + domain constants.

Imports from helpers.py for value coercion in validators. Anything that needs
these models or constants imports from here.
"""
from typing import Optional
from pydantic import BaseModel, field_validator

from helpers import coerce_optional, coerce_number, normalize_grade


# 6 quality criteria the AI agent is graded against on every call (homegrown audit pipeline).
# Order is the canonical display order. Priority drives both flagging logic and
# the dashboard's severity-sorted bars.
#
# Note on "asks_first": real freight brokers ask the carrier their target rate
# BEFORE quoting their own number — this gives the broker information without
# tipping their hand. It's a core skill, not a nice-to-have.
CRITERIA = [
    {
        "id": "fmcsa_first",
        "label": "Carrier verification compliance",
        "short_label": "Verified",
        "description": "Agent verified MC with FMCSA before sharing any load details.",
        "priority": "high",
    },
    {
        "id": "asks_first",
        "label": "Asks for carrier's target rate first",
        "short_label": "Asks first",
        "description": "Agent asked the carrier what rate they were looking for before quoting any number, so the broker doesn't reveal their position before learning the carrier's.",
        "priority": "high",
    },
    {
        "id": "respects_ceiling",
        "label": "Rate discipline",
        "short_label": "Ceiling",
        "description": "Agent never quoted ABOVE the load's maximum_rate (broker's ceiling). Quoting above means broker margin is gone.",
        "priority": "high",
    },
    {
        "id": "confirms_carrier",
        "label": "Identity confirmation",
        "short_label": "Confirmed",
        "description": "Agent read back the FMCSA-returned carrier name and got confirmation.",
        "priority": "high",
    },
    {
        "id": "walks_after_3",
        "label": "Negotiation cap adherence",
        "short_label": "Cap",
        "description": "Agent walked away gracefully if negotiation reached the 3-round cap.",
        "priority": "medium",
    },
    {
        "id": "transfer_message",
        "label": "Handoff completion",
        "short_label": "Handoff",
        "description": "Agent played the mock transfer message when a deal was agreed.",
        "priority": "medium",
    },
]
CRITERIA_BY_ID = {c["id"]: c for c in CRITERIA}


class CallRecord(BaseModel):
    """Webhook payload from the HappyRobot workflow's `Send to Dashboard` node.

    The validators below coerce HappyRobot's loose stringly-typed inputs ("$2,100",
    "null", "PASS") into typed values. Anything unparseable becomes None so a
    single field problem doesn't take down the whole ingest.
    """
    # Carrier + load context
    mc_number: Optional[str] = None
    carrier_name: Optional[str] = None
    reference_number: Optional[str] = None
    fmcsa_eligible: Optional[str] = None

    # Rate negotiation outcome
    loadboard_rate: Optional[float] = None
    carrier_initial_price: Optional[float] = None  # The first number the carrier asked for
    final_agreed_rate: Optional[float] = None
    num_negotiation_rounds: Optional[int] = None

    # Booking + post-call classification
    booking_decision: Optional[str] = None
    decline_reason: Optional[str] = None
    call_outcome: Optional[str] = None
    sentiment: Optional[str] = None
    call_duration_sec: Optional[int] = None

    # Run identifier — enables transcript drill-through from the dashboard
    run_id: Optional[str] = None

    # Quality audit (6 criteria, sent flat from the workflow webhook).
    # Each criterion produces a grade + one-line reason from the AI Extract step.
    audit_fmcsa_first_grade: Optional[str] = None
    audit_fmcsa_first_reason: Optional[str] = None
    audit_asks_first_grade: Optional[str] = None
    audit_asks_first_reason: Optional[str] = None
    audit_respects_ceiling_grade: Optional[str] = None
    audit_respects_ceiling_reason: Optional[str] = None
    audit_confirms_carrier_grade: Optional[str] = None
    audit_confirms_carrier_reason: Optional[str] = None
    audit_walks_after_3_grade: Optional[str] = None
    audit_walks_after_3_reason: Optional[str] = None
    audit_transfer_message_grade: Optional[str] = None
    audit_transfer_message_reason: Optional[str] = None

    @field_validator(
        "mc_number", "carrier_name", "reference_number", "booking_decision",
        "decline_reason", "fmcsa_eligible", "call_outcome", "sentiment", "run_id",
        mode="before",
    )
    @classmethod
    def _str_or_none(cls, v):
        return coerce_optional(v)

    @field_validator("loadboard_rate", "carrier_initial_price", "final_agreed_rate", mode="before")
    @classmethod
    def _float_or_none(cls, v):
        return coerce_number(v)

    @field_validator("num_negotiation_rounds", "call_duration_sec", mode="before")
    @classmethod
    def _int_or_none(cls, v):
        n = coerce_number(v)
        return int(n) if n is not None else None

    @field_validator(
        "audit_fmcsa_first_grade", "audit_asks_first_grade", "audit_respects_ceiling_grade",
        "audit_confirms_carrier_grade", "audit_walks_after_3_grade",
        "audit_transfer_message_grade",
        mode="before",
    )
    @classmethod
    def _grade_or_none(cls, v):
        return normalize_grade(v)

    @field_validator(
        "audit_fmcsa_first_reason", "audit_asks_first_reason", "audit_respects_ceiling_reason",
        "audit_confirms_carrier_reason", "audit_walks_after_3_reason",
        "audit_transfer_message_reason",
        mode="before",
    )
    @classmethod
    def _reason_or_none(cls, v):
        return coerce_optional(v)
