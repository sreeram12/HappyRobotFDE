# Architecture & Engineering Notes

Technical companion to [BUILD.md](./BUILD.md). BUILD is the customer-facing description; this doc is for engineering reviewers who want to see how it's actually wired and why specific choices were made.

---

## Stack

- **Backend:** FastAPI + SQLite, packaged in one Docker container, deployed to Google Cloud Run (`us-central1`, `--min-instances 1` to keep the service warm and prevent SQLite split-brain).
- **Voice agent:** HappyRobot platform — 12-node workflow, GPT-5 Mini model on the prompt node, keyterm-tuned STT.
- **Dashboard:** single-file HTML with Tailwind CSS via CDN and Chart.js. No build step. Served from FastAPI at `/dashboard`. API key injected via placeholder substitution at request time (never source-committed).
- **Tests:** pytest, 31 tests covering coercion, schedule math, audit packing, flagged-queue logic.

Total backend code: ~1,050 LOC across 5 Python files plus the dashboard HTML.

---

## System diagram

```
[Carrier (web call)]
       ↓
[HappyRobot Voice Agent — Paul, GPT-5 Mini, keyterm-tuned STT]
   ├── verify_carrier (tool, message="ai")  →  GET /fmcsa/validate
   └── find_loads (tool, message="ai")      →  GET /loads/search
       ↓ (post-call, 4 nodes)
   AI Classify (outcome — 5 tags)
   AI Extract (9 fields incl. carrier_initial_price)
   AI Classify (sentiment — 3 tags)
   AI Extract (Quality Audit — 6 criteria × pass/fail/N-A + reason)
       ↓
   POST /calls/ingest (idempotent via run_id)
       ↓
[FastAPI · SQLite · Cloud Run]
   helpers.py   · coercion, time windows, compute_ceiling()
   models.py    · CallRecord, CRITERIA (6), RATE_CEILING_PCT
   audit.py     · build_audit_results, flag_reason, high_priority filter
   database.py  · schema + 15 seed loads (with maximum_rate column)
   main.py      · 11 route handlers
       ↓
[Dashboard · single-file HTML · Tailwind CDN · Chart.js]
```

---

## Endpoints

```
GET  /loads/search                    – unified ref-or-lane lookup
GET  /loads/{ref}                     – exact load by reference
GET  /fmcsa/validate                  – proxy real FMCSA API (graceful NOT_FOUND)
POST /calls/ingest                    – webhook receiver (defensive Pydantic coercion)
POST /calls/{id}/review               – mark a flagged call reviewed
GET  /dashboard/metrics               – KPIs incl. revenue, walked-away, hour-of-day
GET  /dashboard/calls                 – paginated, filterable call log
GET  /dashboard/flagged               – exception queue with one-line reasons
GET  /dashboard/lane-stats            – per-lane breakdown
GET  /dashboard/audit-summary         – per-criterion pass rates
GET  /healthz                         – health check
GET  /dashboard                       – static HTML (API key injected at request time)
```

All data routes require `x-api-key` header. HTTPS enforced by Cloud Run.

---

## Data model

### `loads` table (15 seeded — Dry Van, Reefer, Flatbed mix, $850–$4,200)

Per challenge spec plus `maximum_rate` (per-load ceiling override). Default if null = `loadboard_rate × RATE_CEILING_PCT` (default `1.15`). Rounded to nearest `RATE_ROUNDING_USD` (default $5) for natural speech.

Two seed overrides demonstrate the per-load mechanism:
- **REF002** (Reefer Fresno → Seattle): 120% override = $5,040 — high-margin lane
- **FLT001** (Flatbed Detroit → St. Louis): 108% override = $2,590 — tight-margin specialty (steel coils + tarps)

The specific override values are illustrative — chosen to prove the mechanism works. Real values come from shipper-side rates, lane density, equipment scarcity, customer-relationship value, and carrier-tier policy.

### `calls` table (populated by webhook ingest)

```
id                     TEXT PRIMARY KEY (UUID; idempotent via run_id)
created_at             TEXT
mc_number              TEXT
carrier_name           TEXT
reference_number       TEXT
loadboard_rate         REAL
carrier_initial_price  REAL    -- carrier's first stated number, captured for analytics
final_agreed_rate      REAL
num_negotiation_rounds INTEGER
booking_decision       TEXT
decline_reason         TEXT
fmcsa_eligible         TEXT
call_outcome           TEXT
sentiment              TEXT
call_duration_sec      INTEGER
audit_results          TEXT  -- JSON: 6 criteria × {grade, reason}
reviewed_at            TEXT
```

### Load API response shape (illustrative)

```json
{
  "load_id": "DRY001",
  "origin": "Chicago, IL",
  "destination": "Atlanta, GA",
  "equipment_type": "Dry Van",
  "loadboard_rate": 2100.0,
  "maximum_rate": 2415.0,
  "negotiation_schedule": [2100, 2250, 2415],
  "weight": 42000,
  "commodity_type": "General Freight",
  "miles": 716
}
```

`maximum_rate` and `negotiation_schedule` are computed server-side so the LLM never does percentage math — the single biggest reliability risk eliminated by design.

---

## Negotiation logic

In freight brokerage, **the broker pays the carrier**. The posted `loadboard_rate` is the broker's preferred (low) rate. The `maximum_rate` ceiling is the most they'll pay. Carriers typically counter HIGHER than posted (10-15% above is industry standard). The schedule walks UP from list to ceiling.

```
Step 0  pitch load details (NOT the rate)
Step 1  ASK carrier their target rate before quoting

If carrier asks ≤ loadboard_rate     → ACCEPT (the "steal")
If carrier asks > loadboard_rate     → counter at step1 = loadboard_rate

Then on each carrier response, apply TIT-FOR-TAT:
  carrier counter ≤ current_offer    → ACCEPT at carrier's number
  carrier moved ≥$50 toward broker   → step UP to next schedule level
  carrier held firm (<$50 movement)  → HOLD, "I've already stretched"
  carrier moved AWAY (raised ask)    → HOLD, call it out

After step 3 (ceiling) and carrier still won't accept → walk gracefully.
```

The schedule is the *ceiling on broker concessions, not a guarantee of them*. A carrier who holds firm doesn't get rewarded with a free move down the schedule.

**Why backend-computed:** LLMs are unreliable at percentage math under voice latency. Quoting "$2,420 because that's 115% of $2,100" — when the actual answer is $2,415 — is the kind of $5 leak that compounds across thousands of calls. Pre-computing means the agent does *comparison* and *rounding*, not *calculation*.

**Why ask first:** skilled brokers learn the carrier's expectation before revealing their own. Captures meaningful margin in the "carrier was willing to take less than posted" case (the "steal" scenario).

**Why tit-for-tat:** straight from the negotiation literature (Axelrod's IPD, Springer 2012 on real-time bilateral negotiation). Prevents "anchor and wait" carriers from dragging the broker through the entire schedule for free.

---

## Quality audit (homegrown)

HappyRobot's native northstar auto-grading is gated to internal accounts. Rather than leaving that capability dormant, the workflow includes a final AI Extract node that grades every transcript against six criteria, with priority ranking. High-priority failures route to the supervisor's "Needs your review" queue.

| # | Criterion id | Label | Priority |
|---|---|---|---|
| 1 | `fmcsa_first` | Carrier verification compliance | High |
| 2 | `asks_first` | Asks for carrier's target rate first | High |
| 3 | `respects_ceiling` | Rate discipline (never quote above `maximum_rate`) | High |
| 4 | `confirms_carrier` | Identity confirmation | High |
| 5 | `walks_after_3` | Negotiation cap adherence | Medium |
| 6 | `transfer_message` | Handoff completion | Medium |

Each grade is `pass | fail | not_applicable` with a one-line reason. N/A grades are excluded from pass-rate calculation.

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `API_KEY` | yes | Header value clients must send as `x-api-key` for any data route |
| `FMCSA_WEB_KEY` | yes | FMCSA registry key for carrier verification |
| `RATE_CEILING_PCT` | no | Default ceiling as fraction of `loadboard_rate` (default `1.15`) |
| `RATE_ROUNDING_USD` | no | Round computed ceilings to nearest $X for natural speech (default `5`) |

Per-load `maximum_rate` overrides the default percentage.

---

## Deployment

`backend/deploy.sh` is idempotent. It:

1. Creates / reuses a GCP project (`PROJECT_ID` env var, or auto-generated)
2. Auto-links the first available billing account
3. Enables Run, Cloud Build, Artifact Registry APIs
4. Grants Cloud Build's service account the IAM permissions it needs (cloudbuild.builds.builder, artifactregistry.writer, storage.objectViewer, run.developer)
5. Builds and deploys with `--min-instances 1 --max-instances 1` (PoC SQLite constraint)
6. Reads API_KEY and FMCSA_WEB_KEY from `backend/.env`

```bash
cd backend
bash deploy.sh                                 # first time
PROJECT_ID=happyrobot-fde-XXXX bash deploy.sh  # re-deploy to existing project
```

Dockerfile honors Cloud Run's `$PORT` env var.

---

## Key engineering decisions

The full chronological log lives outside the public deliverable. These five capture the most load-bearing thinking:

### 1. Backend pre-computes the negotiation schedule (LLM never does math)

Original biggest reliability risk: a Turbo-class LLM hallucinating "85% of $2,100 is $1,800" mid-call. Solved by computing `maximum_rate` and `negotiation_schedule` server-side and shipping them as fields in the load API response. Agent does only comparison and rounding. Same architectural pattern as production AI systems that need numeric reliability — keep math in code, keep language in the LLM.

### 2. Floor → ceiling inversion (mid-development course-correction)

The PoC's first version had this backwards: a "floor" the broker wouldn't sell below, with the agent conceding DOWN. Wrong for real freight — the broker PAYS the carrier; carriers counter HIGHER than posted; the broker has a CEILING and concedes UP. Caught mid-development via web research (Freight 360, Truckstop, DAT). Reversed every model, schema field, audit criterion, and prompt section in one focused pass. Demonstrates that even with explicit modeling decisions captured in design docs, fundamental assumptions can be wrong — and that customer-driven course-correction is real signal.

### 3. Tit-for-tat + walk-away pressure language

After the inversion, a fixed-per-round concession schedule had a hole: a carrier could anchor at $2,400, hold firm, and watch the broker concede through the entire schedule for free. Replaced with tit-for-tat — broker only steps UP when the carrier moves toward broker by ≥$50 since prior round. Sources: Axelrod's iterated prisoner's dilemma work, Springer 2012 on real-time bilateral negotiation, LGI broker tactics articles. Walk-away pressure language ("close to my ceiling," "got other carriers waiting") added at each step — pure prompt addition, mimics how human brokers create urgency without further concession.

### 4. Homegrown quality-audit pipeline

HappyRobot's native northstar auto-grading is gated to internal accounts. Three options were considered: (a) leave the criteria as defined-but-ungraded and frame in the demo as "production-ready, audit enablement is a deployment step," (b) build our own pipeline, (c) drop the criteria. Picked (b) — demonstrates engineering depth, gives the dashboard a concrete "look at the pass rates per criterion" moment, and routes failures into the existing flagged-queue workflow. The criteria port cleanly to the platform feature when it's enabled — same names, same descriptions, same priorities.

### 5. Production safeguards: stuck-call exit (built) + double-booking (deferred)

Two production-readiness questions surfaced during pre-test review:
- **Stuck-call exit**: a caller who keeps saying "sorry, I didn't catch that" could keep the agent on the line indefinitely. Added prompt-level call-bounding rules (3 clarification attempts → polite exit; off-topic redirect once, exit on next two consecutive turns; negotiation cap reinforced).
- **Double-booking**: explicitly NOT built. The production-correct answer doesn't live in our backend at all — load state is owned by the broker's TMS (McLeod, AscendTMS, etc.). Our SQLite is a stand-in. Delegating to TMS is the production architecture; mocking it in our backend would build a half-feature that competes with the real pattern.

---

## Reliability & security implementation

- **HTTPS everywhere** — Cloud Run with a Google-managed cert.
- **API key authentication** on every data route. Keys in env vars, never in source. Dashboard HTML uses placeholder substitution; real key injected at request time.
- **FMCSA proxy with graceful degradation** — if the registry is unreachable or returns malformed data, returns NOT_FOUND rather than 500.
- **Idempotent webhook ingest** — `run_id` is the dedupe key; HappyRobot retry on 5xx returns the existing record with `duplicate: true` rather than creating a second row.
- **Defensive type coercion** at the trust boundary via Pydantic `field_validator`s — handles "null" strings, "$2,100" → `2100`, "N/A" grade values, empty strings, etc.
- **31 unit tests** covering coercion, schedule math, audit packing, flagged-queue filtering, and flag-reason composition. CI not wired (PoC scope) but `PYTHONPATH=. pytest tests/ -v` is the local entry point.

PoC limitations a real engagement would address:
- Migrate from SQLite to Cloud SQL (Postgres) for write-availability, concurrent writes, and historical retention beyond a single Cloud Run revision
- Per-key rate limiting on the API
- Structured logging with run-ID correlation for debugging
- A fallback storage path for webhook failures (e.g., dead-letter queue)
- Real call transfer via SIP trunk vs. the mock message
- Authentication beyond static API key (OAuth, per-user dashboard auth)

---

## What's out of scope

- Real call transfer (mocked per challenge spec — real warm-transfer requires phone-number purchase + SIP trunk)
- Multi-load negotiation in a single call (one load per call)
- Outbound or callback flows
- CRM/TMS integration (read or write)
- Multi-tenant / multi-broker support (single Acme tenant)
- Load availability state (loads stay static; production answer is TMS delegation, captured under decision #5 above)
- Carrier history / repeat-caller intelligence (captured as a future-work design — playbooks that vary opening anchor + tone but never the ceiling)
