"""Tests for the value-coercion + ceiling-math helpers.

These functions sit at the trust boundary between the HappyRobot webhook (which
emits stringly-typed values like '$2,100', 'null', 'PASS') and our typed Pydantic
model. If they break silently, the dashboard quietly displays wrong numbers.
"""
import os
# Pin a known ceiling before importing helpers so compute_ceiling's defaults are deterministic
os.environ.setdefault("RATE_CEILING_PCT", "1.15")

from helpers import (
    coerce_optional, coerce_number, normalize_grade,
    compute_ceiling, compute_negotiation_schedule,
)


# ---------------------------------------------------------------------------
# coerce_optional — "this could be null and arrive as 50 different forms"
# ---------------------------------------------------------------------------

def test_coerce_optional_passes_through_real_values():
    assert coerce_optional("Acme Trucking") == "Acme Trucking"
    assert coerce_optional("123") == "123"
    assert coerce_optional(42) == 42

def test_coerce_optional_treats_empty_strings_as_none():
    assert coerce_optional("") is None
    assert coerce_optional("   ") is None

def test_coerce_optional_treats_null_strings_as_none():
    assert coerce_optional("null") is None
    assert coerce_optional("NULL") is None
    assert coerce_optional("None") is None
    assert coerce_optional("n/a") is None
    assert coerce_optional("N/A") is None

def test_coerce_optional_strips_whitespace():
    assert coerce_optional("  Acme  ") == "Acme"


# ---------------------------------------------------------------------------
# coerce_number — "anything number-shaped, including '$2,100'"
# ---------------------------------------------------------------------------

def test_coerce_number_handles_clean_input():
    assert coerce_number(2100) == 2100.0
    assert coerce_number(2100.5) == 2100.5
    assert coerce_number("2100") == 2100.0

def test_coerce_number_strips_currency_and_commas():
    assert coerce_number("$2,100") == 2100.0
    assert coerce_number("$1,950.00") == 1950.0
    assert coerce_number("$ 1,234.56") == 1234.56

def test_coerce_number_returns_none_on_garbage():
    assert coerce_number("null") is None
    assert coerce_number("") is None
    assert coerce_number("not a number") is None
    assert coerce_number("-") is None
    assert coerce_number(None) is None


# ---------------------------------------------------------------------------
# normalize_grade — collapses LLM grade output to canonical pass/fail/n_a
# ---------------------------------------------------------------------------

def test_normalize_grade_canonical_forms():
    assert normalize_grade("pass") == "pass"
    assert normalize_grade("fail") == "fail"
    assert normalize_grade("not_applicable") == "not_applicable"

def test_normalize_grade_handles_case_and_synonyms():
    assert normalize_grade("PASS") == "pass"
    assert normalize_grade("Passed") == "pass"
    assert normalize_grade("yes") == "pass"
    assert normalize_grade("FAILED") == "fail"
    assert normalize_grade("no") == "fail"
    assert normalize_grade("N/A") == "not_applicable"
    assert normalize_grade("not applicable") == "not_applicable"

def test_normalize_grade_returns_none_on_unrecognized():
    assert normalize_grade(None) is None
    assert normalize_grade("") is None
    assert normalize_grade("maybe") is None


# ---------------------------------------------------------------------------
# compute_ceiling — broker's max-pay rate (replaces the old floor logic)
# ---------------------------------------------------------------------------

def test_compute_ceiling_default_pct_when_no_override():
    # Default RATE_CEILING_PCT = 1.15, RATE_ROUNDING_USD = 5
    # 2100 * 1.15 = 2415 → already on $5 boundary
    assert compute_ceiling(2100) == 2415

def test_compute_ceiling_rounds_override_to_nearest_5():
    # Explicit override that requires rounding — avoids floating-point quirks
    # where x * 1.15 isn't exactly representable.
    assert compute_ceiling(2100, explicit_override=2412) == 2410  # rounds 2412 down to 2410

def test_compute_ceiling_honors_per_load_override():
    # Broker may set tighter or looser ceilings per lane economics
    assert compute_ceiling(4200, explicit_override=5040) == 5040  # 120% high-margin reefer
    assert compute_ceiling(2400, explicit_override=2590) == 2590  # 108% tight-margin steel

def test_compute_ceiling_returns_none_on_missing_rate():
    assert compute_ceiling(None) is None
    assert compute_ceiling(0) is None

def test_compute_ceiling_override_zero_falls_back_to_default():
    # An explicit override of 0 (not >0) shouldn't replace the default
    assert compute_ceiling(2100, explicit_override=0) == 2415


# ---------------------------------------------------------------------------
# compute_negotiation_schedule — broker's 3-step concession path UP toward ceiling
# ---------------------------------------------------------------------------

def test_negotiation_schedule_default_ceiling():
    # DRY001 example: posted $2,100, default 115% ceiling = $2,415
    # step1 = $2,100 (round to $50); step2 = midpoint($2,100, $2,415) = $2,257.5 → $2,250
    # step3 = $2,415
    schedule = compute_negotiation_schedule(2100)
    assert schedule == [2100, 2250, 2415]
    # Always monotonically non-decreasing toward ceiling
    assert schedule[0] <= schedule[1] <= schedule[2]

def test_negotiation_schedule_high_margin_override():
    # REF002: posted $4,200, 120% override ceiling = $5,040
    # step1 = $4,200; step2 = midpoint = $4,620 → $4,600; step3 = $5,040
    schedule = compute_negotiation_schedule(4200, explicit_override=5040)
    assert schedule == [4200, 4600, 5040]

def test_negotiation_schedule_tight_margin_override():
    # FLT001: posted $2,400, 108% override ceiling = $2,590
    # step1 = $2,400; step2 = midpoint = $2,495 → $2,500; step3 = $2,590
    schedule = compute_negotiation_schedule(2400, explicit_override=2590)
    assert schedule == [2400, 2500, 2590]

def test_negotiation_schedule_returns_none_on_missing_rate():
    assert compute_negotiation_schedule(None) is None
    assert compute_negotiation_schedule(0) is None

def test_negotiation_schedule_returns_none_when_ceiling_at_or_below_posted():
    # Pathological case — degenerate (carrier ceiling can't be at or below posted)
    assert compute_negotiation_schedule(2100, explicit_override=2100) is None
    assert compute_negotiation_schedule(2100, explicit_override=2000) is None

def test_negotiation_schedule_never_above_ceiling():
    # Even with weird rounding, no step ever rises above the ceiling
    schedule = compute_negotiation_schedule(2100)
    assert all(s <= 2415 for s in schedule)
