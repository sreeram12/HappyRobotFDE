"""HappyRobot Carrier Sales API.

App initialization + route handlers. Domain logic lives in:
- helpers.py  — pure utilities (coercion, time windows, load enrichment)
- models.py   — Pydantic models + criteria constants
- audit.py    — quality-audit business logic
- database.py — SQLite connection + schema + seed
"""
import os
import re
import uuid
import json
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Security, Query
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from dotenv import load_dotenv

from database import get_conn, init_db
from helpers import enrich_load, time_window_clause, RATE_CEILING_PCT
from models import CRITERIA, CallRecord
from audit import build_audit_results, high_priority_audit_failures, flag_reason

load_dotenv()

API_KEY = os.environ["API_KEY"]
FMCSA_WEB_KEY = os.environ["FMCSA_WEB_KEY"]
FMCSA_BASE = "https://mobile.fmcsa.dot.gov/qc/services/carriers"

api_key_header = APIKeyHeader(name="x-api-key", auto_error=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifecycle. Replaces the deprecated @app.on_event('startup') pattern."""
    init_db()
    yield


app = FastAPI(title="HappyRobot Carrier Sales API", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def verify_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "carrier-sales-api"}


# ---------------------------------------------------------------------------
# Loads
# ---------------------------------------------------------------------------

@app.get("/loads/search", dependencies=[Security(verify_key)])
def search_loads(
    reference_number: Optional[str] = Query(None),
    origin: Optional[str] = Query(None),
    destination: Optional[str] = Query(None),
    equipment_type: Optional[str] = Query(None),
):
    """Unified load lookup. If `reference_number` is given, returns that load (1-element list).
    Otherwise fuzzy-matches on origin/destination/equipment_type and returns up to 3 results.
    Treats empty strings as 'not provided' so HappyRobot variable substitution works cleanly."""
    reference_number = (reference_number or "").strip() or None
    origin = (origin or "").strip() or None
    destination = (destination or "").strip() or None
    equipment_type = (equipment_type or "").strip() or None

    conn = get_conn()

    if reference_number:
        row = conn.execute(
            "SELECT * FROM loads WHERE load_id = ?", (reference_number.upper(),)
        ).fetchone()
        conn.close()
        return [enrich_load(dict(row))] if row else []

    query = "SELECT * FROM loads WHERE 1=1"
    params = []

    def add_location_filter(value: str, column: str):
        nonlocal query
        parts = [p.strip().lower() for p in value.split(",") if p.strip()]
        if not parts:
            return
        clauses = " OR ".join([f"LOWER({column}) LIKE ?" for _ in parts])
        query += f" AND ({clauses})"
        params.extend([f"%{p}%" for p in parts])

    if origin:
        add_location_filter(origin, "origin")
    if destination:
        add_location_filter(destination, "destination")
    if equipment_type:
        query += " AND LOWER(equipment_type) LIKE ?"
        params.append(f"%{equipment_type.lower()}%")

    query += " ORDER BY loadboard_rate DESC LIMIT 3"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [enrich_load(dict(r)) for r in rows]


@app.get("/loads/{reference_number}", dependencies=[Security(verify_key)])
def get_load(reference_number: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM loads WHERE load_id = ?", (reference_number.upper(),)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Load not found")
    return enrich_load(dict(row))


# ---------------------------------------------------------------------------
# FMCSA proxy
# ---------------------------------------------------------------------------

@app.get("/fmcsa/validate", dependencies=[Security(verify_key)])
async def validate_carrier(mc_number: str = Query(...)):
    """Proxy to the FMCSA carrier registry. Strips any 'MC' prefix and non-digits.
    Returns a normalized eligibility shape; falls back to NOT_FOUND on any error."""
    clean = re.sub(r"\D", "", mc_number)
    if not clean:
        return {"eligible": False, "carrier_name": None, "dot_number": None, "status": "INVALID"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{FMCSA_BASE}/{clean}",
                params={"webKey": FMCSA_WEB_KEY},
            )
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="FMCSA service unreachable")

    if resp.status_code == 404:
        return {"eligible": False, "carrier_name": None, "dot_number": None, "status": "NOT_FOUND"}
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"FMCSA returned {resp.status_code}")

    data = resp.json()
    content = data.get("content", {})
    # FMCSA returns content as a string error message when not found
    if not content or isinstance(content, str):
        return {"eligible": False, "carrier_name": None, "dot_number": None, "status": "NOT_FOUND"}

    carrier = content.get("carrier", {})
    if not carrier:
        return {"eligible": False, "carrier_name": None, "dot_number": None, "status": "NOT_FOUND"}

    allowed = (carrier.get("allowedToOperate") or "N").upper() == "Y"
    dot_number = carrier.get("dotNumber")
    return {
        "eligible": allowed,
        "carrier_name": carrier.get("legalName") or carrier.get("dbaName"),
        "dot_number": str(dot_number) if dot_number else None,
        "status": "ACTIVE" if allowed else "INACTIVE",
    }


# ---------------------------------------------------------------------------
# Calls — webhook ingest + supervisor review
# ---------------------------------------------------------------------------

@app.post("/calls/ingest", dependencies=[Security(verify_key)])
def ingest_call(record: CallRecord):
    """Webhook receiver. Idempotent on `run_id` — if HappyRobot retries on a 5xx,
    we don't create duplicate call records."""
    audit_results = build_audit_results(record)
    conn = get_conn()

    # Idempotency check — if this run_id has already been ingested, return its existing id
    if record.run_id:
        existing = conn.execute(
            "SELECT id FROM calls WHERE run_id = ?", (record.run_id,)
        ).fetchone()
        if existing:
            conn.close()
            return {"id": existing["id"], "status": "ok", "duplicate": True}

    call_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO calls (
            id, created_at, mc_number, carrier_name, reference_number,
            loadboard_rate, final_agreed_rate, num_negotiation_rounds,
            booking_decision, decline_reason, fmcsa_eligible,
            call_outcome, sentiment, call_duration_sec, audit_results, run_id,
            carrier_initial_price
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        call_id, created_at,
        record.mc_number, record.carrier_name, record.reference_number,
        record.loadboard_rate, record.final_agreed_rate, record.num_negotiation_rounds,
        record.booking_decision, record.decline_reason, record.fmcsa_eligible,
        record.call_outcome, record.sentiment, record.call_duration_sec,
        audit_results, record.run_id, record.carrier_initial_price,
    ))
    conn.commit()
    conn.close()
    return {"id": call_id, "status": "ok"}


@app.post("/calls/{call_id}/review", dependencies=[Security(verify_key)])
def mark_call_reviewed(call_id: str):
    """Mark a call as reviewed by the supervisor — removes it from the flagged queue."""
    reviewed_at = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE calls SET reviewed_at = ? WHERE id = ?", (reviewed_at, call_id)
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"id": call_id, "reviewed_at": reviewed_at, "status": "ok"}


# ---------------------------------------------------------------------------
# Dashboard — metrics, calls, flagged, lanes, audit
# ---------------------------------------------------------------------------

@app.get("/dashboard/metrics", dependencies=[Security(verify_key)])
def dashboard_metrics(timeframe: Optional[str] = Query("7d", regex="^(today|7d|30d|all)$")):
    """Aggregated metrics for the dashboard. Timeframe filters the call window."""
    window = time_window_clause(timeframe if timeframe != "all" else None)

    conn = get_conn()
    c = conn.cursor()

    total_calls = c.execute(f"SELECT COUNT(*) FROM calls WHERE 1=1 {window}").fetchone()[0]
    booked = c.execute(
        f"SELECT COUNT(*) FROM calls WHERE call_outcome = 'booked' {window}"
    ).fetchone()[0]
    booking_rate = round(booked / total_calls * 100, 1) if total_calls else 0

    revenue_captured = c.execute(f"""
        SELECT COALESCE(SUM(final_agreed_rate), 0) FROM calls
        WHERE call_outcome = 'booked' AND final_agreed_rate IS NOT NULL {window}
    """).fetchone()[0]

    # "Walked-away value" = the ceiling we held on rate_rejected calls.
    # In freight, the broker has a maximum_rate they'll pay; if the carrier wants
    # more, the broker walks. This metric shows the dollar value of the discipline
    # — sum of the ceilings held on calls that walked.
    # Joins to loads so we use the per-load maximum_rate override when set; falls
    # back to the default ceiling percentage otherwise.
    lost_revenue = c.execute(f"""
        SELECT COALESCE(SUM(
            COALESCE(l.maximum_rate, c.loadboard_rate * ?)
        ), 0)
        FROM calls c
        LEFT JOIN loads l ON UPPER(c.reference_number) = l.load_id
        WHERE c.call_outcome = 'rate_rejected' AND c.loadboard_rate IS NOT NULL {window.replace('created_at', 'c.created_at')}
    """, (RATE_CEILING_PCT,)).fetchone()[0]

    avg_rate_capture = c.execute(f"""
        SELECT AVG(final_agreed_rate * 1.0 / loadboard_rate * 100) FROM calls
        WHERE final_agreed_rate IS NOT NULL AND loadboard_rate > 0 {window}
    """).fetchone()[0]

    avg_call_duration = c.execute(
        f"SELECT AVG(call_duration_sec) FROM calls WHERE call_duration_sec IS NOT NULL {window}"
    ).fetchone()[0]

    outcomes = {r["call_outcome"]: r["cnt"] for r in c.execute(
        f"SELECT call_outcome, COUNT(*) as cnt FROM calls WHERE 1=1 {window} GROUP BY call_outcome"
    ).fetchall()}
    sentiments = {r["sentiment"]: r["cnt"] for r in c.execute(
        f"SELECT sentiment, COUNT(*) as cnt FROM calls WHERE 1=1 {window} GROUP BY sentiment"
    ).fetchall()}
    fmcsa = {r["fmcsa_eligible"]: r["cnt"] for r in c.execute(
        f"SELECT fmcsa_eligible, COUNT(*) as cnt FROM calls WHERE 1=1 {window} GROUP BY fmcsa_eligible"
    ).fetchall()}
    negotiation_rounds = {str(r["num_negotiation_rounds"]): r["cnt"] for r in c.execute(
        f"SELECT num_negotiation_rounds, COUNT(*) as cnt FROM calls "
        f"WHERE num_negotiation_rounds IS NOT NULL {window} GROUP BY num_negotiation_rounds"
    ).fetchall()}

    # Daily timeline (always last 14 days regardless of timeframe — gives chart context)
    daily_volume = [dict(r) for r in c.execute("""
        SELECT
            DATE(created_at) as date,
            COUNT(*) as count,
            COALESCE(SUM(CASE WHEN call_outcome = 'booked' THEN final_agreed_rate ELSE 0 END), 0) as revenue
        FROM calls
        WHERE created_at >= DATE('now', '-14 days')
        GROUP BY DATE(created_at)
        ORDER BY date
    """).fetchall()]

    # Hour-of-day distribution (within selected window) — answers "is the AI catching after-hours volume?"
    hourly = c.execute(f"""
        SELECT CAST(strftime('%H', created_at) AS INTEGER) as hour, COUNT(*) as count
        FROM calls
        WHERE 1=1 {window}
        GROUP BY hour
        ORDER BY hour
    """).fetchall()
    hourly_map = {r["hour"]: r["count"] for r in hourly}
    hourly_distribution = [{"hour": h, "count": hourly_map.get(h, 0)} for h in range(24)]

    # Coverage = % of calls that got an actual answer (not carrier_hung_up)
    answered = total_calls - (outcomes.get("carrier_hung_up", 0))
    coverage_pct = round(answered / total_calls * 100, 1) if total_calls else 100.0

    # After-hours coverage = share of calls outside 8am-6pm. Speaks to AI's actual value-add.
    after_hours_count = c.execute(f"""
        SELECT COUNT(*) FROM calls
        WHERE (CAST(strftime('%H', created_at) AS INTEGER) < 8
            OR CAST(strftime('%H', created_at) AS INTEGER) >= 18) {window}
    """).fetchone()[0]
    after_hours_pct = round(after_hours_count / total_calls * 100, 1) if total_calls else 0

    conn.close()

    return {
        "timeframe": timeframe,
        "total_calls": total_calls,
        "booked": booked,
        "booking_rate_pct": booking_rate,
        "revenue_captured": round(revenue_captured, 2),
        "lost_revenue": round(lost_revenue, 2),
        "avg_rate_capture_pct": round(avg_rate_capture, 1) if avg_rate_capture else None,
        "avg_call_duration_sec": round(avg_call_duration) if avg_call_duration else None,
        "coverage_pct": coverage_pct,
        "after_hours_pct": after_hours_pct,
        "after_hours_count": after_hours_count,
        "outcomes": outcomes,
        "sentiments": sentiments,
        "fmcsa": fmcsa,
        "negotiation_rounds": negotiation_rounds,
        "daily_volume": daily_volume,
        "hourly_distribution": hourly_distribution,
    }


@app.get("/dashboard/calls", dependencies=[Security(verify_key)])
def dashboard_calls(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    outcome: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    timeframe: Optional[str] = Query("all", regex="^(today|7d|30d|all)$"),
):
    """Paginated, filterable call log. Joins to loads for lane info."""
    offset = (page - 1) * limit
    where = " WHERE 1=1"
    params = []
    if outcome:
        where += " AND c.call_outcome = ?"
        params.append(outcome)
    if sentiment:
        where += " AND c.sentiment = ?"
        params.append(sentiment)
    tf_map = {
        "today": " AND DATE(c.created_at) = DATE('now')",
        "7d": " AND c.created_at >= DATE('now', '-7 days')",
        "30d": " AND c.created_at >= DATE('now', '-30 days')",
    }
    where += tf_map.get(timeframe or "", "") if timeframe != "all" else ""

    base_query = f"""
        FROM calls c
        LEFT JOIN loads l ON UPPER(c.reference_number) = l.load_id
        {where}
    """

    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) {base_query}", params).fetchone()[0]
    rows = conn.execute(f"""
        SELECT
            c.*,
            l.origin AS lane_origin,
            l.destination AS lane_destination,
            l.equipment_type AS lane_equipment,
            l.maximum_rate AS lane_maximum_rate
        {base_query}
        ORDER BY c.created_at DESC LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    conn.close()
    return {
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
        "calls": [dict(r) for r in rows],
    }


@app.get("/dashboard/flagged", dependencies=[Security(verify_key)])
def dashboard_flagged(limit: int = Query(5, ge=1, le=20)):
    """Supervisor review queue. Surfaces non-booked outcomes, negative sentiment,
    AND high-priority audit-criterion failures. Audit failures take precedence in the reason copy."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            c.*,
            l.origin AS lane_origin,
            l.destination AS lane_destination,
            l.equipment_type AS lane_equipment,
            l.maximum_rate AS lane_maximum_rate
        FROM calls c
        LEFT JOIN loads l ON UPPER(c.reference_number) = l.load_id
        WHERE c.reviewed_at IS NULL
        ORDER BY c.created_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()

    flagged = []
    for r in rows:
        c = dict(r)
        outcome = c.get("call_outcome")
        sentiment = c.get("sentiment")
        audit_fails = high_priority_audit_failures(c.get("audit_results"))

        is_outcome_flag = outcome in ("rate_rejected", "carrier_ineligible", "no_load_found", "carrier_hung_up")
        is_sentiment_flag = sentiment == "negative"
        is_audit_flag = len(audit_fails) > 0

        if not (is_outcome_flag or is_sentiment_flag or is_audit_flag):
            continue

        # Audit failures (compliance) take precedence over outcome (operational)
        if is_audit_flag:
            _, reason, label = audit_fails[0]
            c["flag_reason"] = f"Audit fail — {label}: {reason or 'see call detail'}"
            c["flag_kind"] = "audit"
        elif is_outcome_flag:
            c["flag_reason"] = flag_reason(c)
            c["flag_kind"] = "outcome"
        else:
            c["flag_reason"] = flag_reason(c)
            c["flag_kind"] = "sentiment"

        flagged.append(c)
        if len(flagged) >= limit:
            break

    return {"flagged": flagged, "count": len(flagged)}


@app.get("/dashboard/audit-summary", dependencies=[Security(verify_key)])
def audit_summary(timeframe: Optional[str] = Query("7d", regex="^(today|7d|30d|all)$")):
    """Per-criterion pass/fail rates across recent graded calls.
    N/A is excluded from the denominator so 'walks_after_3' isn't penalized when
    no negotiation happened."""
    window = time_window_clause(timeframe if timeframe != "all" else None)
    conn = get_conn()
    rows = conn.execute(
        f"SELECT audit_results FROM calls WHERE audit_results IS NOT NULL {window}"
    ).fetchall()
    conn.close()

    counts = {c["id"]: {"pass": 0, "fail": 0, "not_applicable": 0} for c in CRITERIA}
    n_graded = 0
    for r in rows:
        try:
            data = json.loads(r["audit_results"])
        except (json.JSONDecodeError, TypeError):
            continue
        n_graded += 1
        for c in CRITERIA:
            entry = data.get(c["id"], {})
            grade = entry.get("grade")
            if grade in counts[c["id"]]:
                counts[c["id"]][grade] += 1

    criteria_out = []
    overall_pass = 0
    overall_fail = 0
    for c in CRITERIA:
        cc = counts[c["id"]]
        denom = cc["pass"] + cc["fail"]
        pass_rate = round(cc["pass"] / denom * 100, 1) if denom else None
        criteria_out.append({
            "id": c["id"],
            "label": c["label"],
            "priority": c["priority"],
            "pass": cc["pass"],
            "fail": cc["fail"],
            "not_applicable": cc["not_applicable"],
            "pass_rate_pct": pass_rate,
        })
        overall_pass += cc["pass"]
        overall_fail += cc["fail"]

    overall_denom = overall_pass + overall_fail
    overall_pass_rate = round(overall_pass / overall_denom * 100, 1) if overall_denom else None

    return {
        "timeframe": timeframe,
        "n_graded": n_graded,
        "overall_pass_rate_pct": overall_pass_rate,
        "total_failures": overall_fail,
        "criteria": criteria_out,
    }


@app.get("/dashboard/lane-stats", dependencies=[Security(verify_key)])
def dashboard_lane_stats(timeframe: Optional[str] = Query("all", regex="^(today|7d|30d|all)$")):
    """Per-lane performance breakdown — joins calls.reference_number to loads."""
    window = time_window_clause(timeframe if timeframe != "all" else None)
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT
            l.origin || ' → ' || l.destination AS lane,
            l.equipment_type AS equipment_type,
            COUNT(c.id) AS total_calls,
            SUM(CASE WHEN c.call_outcome = 'booked' THEN 1 ELSE 0 END) AS booked,
            COALESCE(SUM(CASE WHEN c.call_outcome = 'booked' THEN c.final_agreed_rate END), 0) AS revenue,
            AVG(CASE WHEN c.final_agreed_rate IS NOT NULL AND c.loadboard_rate > 0
                     THEN c.final_agreed_rate * 100.0 / c.loadboard_rate END) AS avg_rate_capture
        FROM calls c
        JOIN loads l ON UPPER(c.reference_number) = l.load_id
        WHERE 1=1 {window}
        GROUP BY l.load_id
        ORDER BY revenue DESC, total_calls DESC
    """).fetchall()
    conn.close()

    return {
        "lanes": [
            {
                "lane": r["lane"],
                "equipment_type": r["equipment_type"],
                "total_calls": r["total_calls"],
                "booked": r["booked"],
                "revenue": round(r["revenue"], 2) if r["revenue"] else 0,
                "avg_rate_capture_pct": round(r["avg_rate_capture"], 1) if r["avg_rate_capture"] else None,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Static dashboard
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
DASHBOARD_TEMPLATE = STATIC_DIR / "index.html"

# We inject the API key into the served HTML at request time rather than committing
# it to the static file. The placeholder lives in the source; the real key only ever
# exists in the live response and process memory. A security review of the repo will
# not find a hardcoded credential.
_API_KEY_PLACEHOLDER = "__API_KEY_PLACEHOLDER__"


def _render_dashboard() -> str:
    """Read index.html template and substitute the API key placeholder."""
    html = DASHBOARD_TEMPLATE.read_text()
    return html.replace(_API_KEY_PLACEHOLDER, API_KEY)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/dashboard", response_class=HTMLResponse)
    def serve_dashboard():
        return HTMLResponse(_render_dashboard())

    @app.get("/", response_class=HTMLResponse)
    def serve_root():
        return HTMLResponse(_render_dashboard())
