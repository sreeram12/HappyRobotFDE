"""Quality-audit business logic.

Sits on top of the model + helpers layer. Functions here turn raw audit inputs
(flat fields from the webhook, JSON blobs from the DB) into the shapes the
dashboard endpoints need.
"""
import json
from typing import Optional, List, Tuple

from helpers import compute_ceiling
from models import CRITERIA, CallRecord


def build_audit_results(record: CallRecord) -> Optional[str]:
    """Pack the flat `audit_<criterion>_grade/reason` fields from the webhook
    into a single JSON blob for storage.

    Returns None if no audit fields were populated (so we don't store empty stubs).
    """
    audit = {}
    for c in CRITERIA:
        grade = getattr(record, f"audit_{c['id']}_grade", None)
        reason = getattr(record, f"audit_{c['id']}_reason", None)
        if grade is not None or reason is not None:
            audit[c["id"]] = {"grade": grade, "reason": reason}
    return json.dumps(audit) if audit else None


def high_priority_audit_failures(audit_blob: Optional[str]) -> List[Tuple[str, Optional[str], str]]:
    """Return the high-priority criteria that failed on a given call.

    Used by the flagged endpoint to route audit failures into the supervisor's
    review queue. Each failure is (criterion_id, reason, human_label).
    """
    if not audit_blob:
        return []
    try:
        data = json.loads(audit_blob)
    except (json.JSONDecodeError, TypeError):
        return []
    failures: List[Tuple[str, Optional[str], str]] = []
    for c in CRITERIA:
        if c["priority"] != "high":
            continue
        entry = data.get(c["id"], {})
        if entry.get("grade") == "fail":
            failures.append((c["id"], entry.get("reason"), c["label"]))
    return failures


def flag_reason(c: dict) -> str:
    """One-line human-readable reason this call was flagged for review.

    Picks the most informative explanation based on what's in the row —
    rate-rejected calls get rate context (using the actual per-load ceiling
    when available, falling back to the default), ineligible carriers get the
    FMCSA reason, etc.
    """
    outcome = c.get("call_outcome")
    sentiment = c.get("sentiment")
    if outcome == "rate_rejected":
        rate = c.get("loadboard_rate")
        rounds = c.get("num_negotiation_rounds")
        if rate:
            # Prefer per-load override if the JOIN populated it; else default
            ceiling = compute_ceiling(rate, c.get("lane_maximum_rate"))
            return f"Walked after {rounds or '?'} rounds. Posted ${rate:,.0f}, held ceiling at ${ceiling:,.0f}."
        return f"Walked after {rounds or '?'} rounds of negotiation."
    if outcome == "carrier_ineligible":
        return "FMCSA returned NOT_FOUND or inactive authority."
    if outcome == "no_load_found":
        return "No matching load for this carrier's lane."
    if outcome == "carrier_hung_up":
        return "Carrier disconnected before completion."
    if sentiment == "negative":
        return "Carrier expressed frustration during the call."
    return "Needs supervisor review."
