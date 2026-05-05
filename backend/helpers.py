"""Pure utilities — no domain imports.

Anything that doesn't reference our Pydantic models or business rules lives here.
Used by models.py (validators) and audit.py (business logic), so this module is
the bottom of the import graph.
"""
import os
import re
from typing import Optional, Any

# ---------------------------------------------------------------------------
# Negotiation ceiling configuration
# ---------------------------------------------------------------------------
# In freight brokerage, the broker PAYS the carrier. The posted load rate is
# the broker's preferred (low) starting offer. Carriers typically counter
# HIGHER (10-15% above posted is industry standard). The broker has a CEILING —
# the maximum they're willing to pay before walking away from the deal.
#
# RATE_CEILING_PCT is the default fraction of `loadboard_rate` that the broker
# will pay up to. Per-load `maximum_rate` overrides this when a specific lane
# has different margin economics.
#
# Configurable via env var (e.g. RATE_CEILING_PCT=1.10 for tighter discipline).
RATE_CEILING_PCT = float(os.environ.get("RATE_CEILING_PCT", "1.15"))

# Round computed ceilings/steps to nearest $X for natural-sounding speech.
# Floor speech ("seventeen-eighty-five") and ceiling speech ("twenty-four-fifteen")
# both read better with $5 / $50 rounding than raw floats.
RATE_ROUNDING_USD = int(os.environ.get("RATE_ROUNDING_USD", "5"))

# Step rounding for in-conversation concession offers. $50 reads more naturally
# than $5 when said aloud ("twenty-two-fifty" vs "twenty-two-forty-five").
NEGOTIATION_STEP_ROUNDING = 50


def coerce_optional(value: Any) -> Any:
    """Treat empty strings, 'null', 'None', whitespace-only strings as None.

    The HappyRobot webhook may substitute empty strings for missing variables,
    and the AI Extract step can emit literal 'null' strings. This collapses all
    of those to a real None so Pydantic doesn't fight us.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if v == "" or v.lower() in ("null", "none", "n/a", "na"):
            return None
        return v
    return value


def coerce_number(value: Any) -> Optional[float]:
    """Strip currency symbols, commas, and other crud before float conversion.

    The LLM's Extract step often returns "$2,100" or "1,950.00" as strings — we
    accept anything number-shaped and convert to float, returning None for
    unparseable inputs rather than raising.
    """
    v = coerce_optional(value)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[^\d.\-]", "", v)
        if not cleaned or cleaned in ("-", ".", "-."):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def normalize_grade(value: Any) -> Optional[str]:
    """Coerce the agent's audit-grade output to one of pass / fail / not_applicable.

    Unlike `coerce_optional`, this does NOT treat 'N/A' as a missing-field marker —
    here it's a legitimate value (means 'not_applicable'). So we do our own
    coercion rather than chaining through coerce_optional.
    """
    if value is None:
        return None
    s = str(value).strip().lower().replace(" ", "_").replace("/", "_")
    if not s or s in ("null", "none"):
        return None
    if s in ("pass", "passed", "yes", "true", "ok", "good"):
        return "pass"
    if s in ("fail", "failed", "no", "false", "violation"):
        return "fail"
    if s in ("not_applicable", "n_a", "na", "skip", "skipped"):
        return "not_applicable"
    return None


def compute_ceiling(loadboard_rate: Optional[float], explicit_override: Optional[float] = None) -> Optional[float]:
    """Single source of truth for the broker's negotiation ceiling.

    The ceiling is the MAXIMUM the broker will pay for this load. It exists
    because real freight negotiation has carriers asking for MORE than the
    posted rate, not less — broker holds a ceiling, walks if carrier won't
    come down to it.

    Returns the per-load `maximum_rate` if set; otherwise the default-percentage
    of loadboard_rate (e.g., 115% with the default RATE_CEILING_PCT).
    Rounded to nearest RATE_ROUNDING_USD. Returns None for unparseable input.
    """
    if explicit_override is not None and explicit_override > 0:
        return round(explicit_override / RATE_ROUNDING_USD) * RATE_ROUNDING_USD
    if loadboard_rate and loadboard_rate > 0:
        return round(loadboard_rate * RATE_CEILING_PCT / RATE_ROUNDING_USD) * RATE_ROUNDING_USD
    return None


def compute_negotiation_schedule(loadboard_rate: Optional[float], explicit_override: Optional[float] = None) -> Optional[list]:
    """Compute the broker's 3-step concession schedule for a load.

    Returns [step1, step2, ceiling] — the broker walks UP through these
    on each round of carrier ask, only accepting if the carrier's price
    matches/comes below the broker's current step. Carrier's behavior never
    pulls the broker up faster than this schedule.

    - step1 = posted_rate (broker plants their flag at posted; first concession)
    - step2 = midpoint between posted and ceiling
    - step3 = ceiling (final offer; walk if carrier still wants more)

    Steps round to nearest $50 for natural-sounding speech; ceiling uses
    RATE_ROUNDING_USD (default $5).
    """
    ceiling = compute_ceiling(loadboard_rate, explicit_override)
    if ceiling is None or loadboard_rate is None or loadboard_rate <= 0 or ceiling <= loadboard_rate:
        return None
    gap = ceiling - loadboard_rate
    step1 = round(loadboard_rate / NEGOTIATION_STEP_ROUNDING) * NEGOTIATION_STEP_ROUNDING
    step2 = round((loadboard_rate + gap / 2) / NEGOTIATION_STEP_ROUNDING) * NEGOTIATION_STEP_ROUNDING
    # Defensive: monotonically non-decreasing, never above ceiling
    step1 = min(step1, ceiling)
    step2 = max(step1, min(step2, ceiling))
    return [int(step1), int(step2), int(ceiling)]


def enrich_load(row: dict) -> dict:
    """Resolve the negotiation ceiling + concession schedule for a load.

    Adds `maximum_rate`, `maximum_rate_source`, and `negotiation_schedule`.
    The agent uses `negotiation_schedule` to step UP through concessions on
    each round of carrier ask without doing percentage math at runtime.
    """
    explicit = row.get("maximum_rate")
    rate = row.get("loadboard_rate")
    ceiling = compute_ceiling(rate, explicit)
    row["maximum_rate"] = ceiling
    if ceiling is None:
        row["maximum_rate_source"] = None
    elif explicit is not None and explicit > 0:
        row["maximum_rate_source"] = "load_override"
    else:
        row["maximum_rate_source"] = "default_pct"
    row["negotiation_schedule"] = compute_negotiation_schedule(rate, explicit)
    # Drop any legacy minimum_rate references — model is ceiling-based now
    row.pop("minimum_rate", None)
    row.pop("minimum_rate_source", None)
    return row


def time_window_clause(timeframe: Optional[str]) -> str:
    """SQL fragment limiting `calls.created_at` to a window. Empty string for 'all' or unknown."""
    mapping = {
        "today": " AND DATE(created_at) = DATE('now')",
        "7d": " AND created_at >= DATE('now', '-7 days')",
        "30d": " AND created_at >= DATE('now', '-30 days')",
    }
    return mapping.get(timeframe or "", "")
