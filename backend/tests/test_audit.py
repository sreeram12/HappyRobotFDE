"""Tests for audit business logic — JSON packing, high-priority failure filtering,
and human-readable flag reasons."""
import os
import json
os.environ.setdefault("RATE_CEILING_PCT", "1.15")

from models import CallRecord
from audit import build_audit_results, high_priority_audit_failures, flag_reason


def _make_record(**audit_overrides) -> CallRecord:
    base = {
        "audit_fmcsa_first_grade": "pass",
        "audit_fmcsa_first_reason": "Asked for MC first",
        "audit_asks_first_grade": "pass",
        "audit_asks_first_reason": "Asked carrier their target rate before quoting",
        "audit_respects_ceiling_grade": "pass",
        "audit_respects_ceiling_reason": "Held ceiling — never quoted above max",
        "audit_confirms_carrier_grade": "pass",
        "audit_confirms_carrier_reason": "Confirmed",
        "audit_walks_after_3_grade": "not_applicable",
        "audit_walks_after_3_reason": "Closed in 1 round",
        "audit_transfer_message_grade": "pass",
        "audit_transfer_message_reason": "Said transfer line",
    }
    base.update(audit_overrides)
    return CallRecord(**base)


# ---------------------------------------------------------------------------
# build_audit_results — packs flat webhook fields into a JSON blob
# ---------------------------------------------------------------------------

def test_build_audit_results_packs_all_6_criteria():
    record = _make_record()
    blob = build_audit_results(record)
    assert blob is not None
    data = json.loads(blob)
    assert set(data.keys()) == {
        "fmcsa_first", "asks_first", "respects_ceiling",
        "confirms_carrier", "walks_after_3", "transfer_message",
    }
    assert data["fmcsa_first"] == {"grade": "pass", "reason": "Asked for MC first"}

def test_build_audit_results_returns_none_when_no_audit_data():
    record = CallRecord()  # all audit fields default to None
    assert build_audit_results(record) is None


# ---------------------------------------------------------------------------
# high_priority_audit_failures — feeds the flagged queue
# ---------------------------------------------------------------------------

def test_high_priority_failures_returns_empty_on_all_pass():
    record = _make_record()
    failures = high_priority_audit_failures(build_audit_results(record))
    assert failures == []

def test_high_priority_failures_surfaces_high_priority_only():
    # Fail one high-priority criterion (asks_first) and one medium (transfer_message)
    record = _make_record(
        audit_asks_first_grade="fail",
        audit_asks_first_reason="Quoted price before asking carrier",
        audit_transfer_message_grade="fail",
        audit_transfer_message_reason="Skipped transfer line",
    )
    failures = high_priority_audit_failures(build_audit_results(record))
    # asks_first (high) shows; transfer_message (medium) is filtered out
    assert len(failures) == 1
    crit_id, reason, label = failures[0]
    assert crit_id == "asks_first"
    assert reason == "Quoted price before asking carrier"
    assert "Asks" in label or "carrier" in label.lower()

def test_high_priority_failures_handles_invalid_json():
    assert high_priority_audit_failures("not valid json") == []
    assert high_priority_audit_failures(None) == []
    assert high_priority_audit_failures("") == []


# ---------------------------------------------------------------------------
# flag_reason — human-readable explanation per call
# ---------------------------------------------------------------------------

def test_flag_reason_rate_rejected_uses_default_ceiling():
    c = {"call_outcome": "rate_rejected", "loadboard_rate": 2100, "num_negotiation_rounds": 3}
    reason = flag_reason(c)
    assert "$2,100" in reason
    assert "$2,415" in reason  # default 115% of 2100
    assert "3 rounds" in reason

def test_flag_reason_rate_rejected_uses_per_load_override():
    # When the JOIN populates lane_maximum_rate, use it instead of the default
    c = {
        "call_outcome": "rate_rejected",
        "loadboard_rate": 2400,
        "lane_maximum_rate": 2590,  # FLT001's 108% override
        "num_negotiation_rounds": 3,
    }
    reason = flag_reason(c)
    assert "$2,400" in reason
    assert "$2,590" in reason  # the override

def test_flag_reason_carrier_ineligible():
    c = {"call_outcome": "carrier_ineligible"}
    assert "FMCSA" in flag_reason(c)
    assert "NOT_FOUND" in flag_reason(c) or "inactive" in flag_reason(c)

def test_flag_reason_fallback_for_unknown_outcome():
    c = {"call_outcome": None, "sentiment": None}
    assert flag_reason(c) == "Needs supervisor review."

def test_flag_reason_negative_sentiment_only():
    c = {"call_outcome": "booked", "sentiment": "negative"}
    assert "frustration" in flag_reason(c).lower()
