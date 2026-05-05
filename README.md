# Inbound Carrier Sales Automation

A voice AI agent that handles inbound carrier load inquiries end-to-end — FMCSA verification, rate negotiation, booking, and handoff — with a live supervisor dashboard and a homegrown quality-audit pipeline. Built on [HappyRobot](https://happyrobot.ai) for a Forward Deployed Engineer technical challenge.

**📄 [Read the build description (BUILD.md)](./BUILD.md)** · **🔧 [Engineering notes (ARCHITECTURE.md)](./ARCHITECTURE.md)**

> **Live demo URL** is provided in the submission email rather than published here, to keep the Cloud Run instance from being indexed by public crawlers. If you're a reviewer and the email link doesn't work for any reason, please reach out — `ssandrapati477@gmail.com`.

| | |
|---|---|
| Dashboard | Provided in submission email |
| API | Same host as the dashboard |
| HappyRobot workflow | [`qi4lcsb4k3zj` — FDE Assessment, dev](https://platform.happyrobot.ai/fdesreeramsandrapati/workflows/qi4lcsb4k3zj/editor/ngloab75iopl) (HappyRobot login required) |

---

## What it does

A carrier dials in. The agent (Paul) verifies them with FMCSA, pitches a matching load, **asks the carrier's target rate first**, negotiates inside a per-load ceiling, and hands off on agreement. Every call lands on the supervisor dashboard within seconds — auto-classified outcome and sentiment, 9 extracted fields, 6 quality criteria graded by an LLM judge.

The negotiation is backend-driven so the LLM never does percentage math: a per-load `maximum_rate` ceiling and a 3-step concession schedule are pre-computed server-side. The agent does only **comparison and rounding**, gated by **tit-for-tat** — it concedes only when the carrier moves first. Full strategy, design rationale, and customer-facing details are in **[BUILD.md](./BUILD.md)**.

---

## Architecture

```
Carrier (web call)
   ↓
HappyRobot Voice Agent — Paul (GPT-5 Mini)
   ├── verify_carrier  →  GET /fmcsa/validate  →  FMCSA registry
   └── find_loads      →  GET /loads/search    →  SQLite seed DB
   ↓ (post-call)
AI Classify (outcome) → AI Extract (9 fields) → AI Classify (sentiment) → AI Extract (Quality Audit, 6 criteria)
   ↓
Webhook  →  POST /calls/ingest  →  SQLite  →  Dashboard (HTML + Tailwind + Chart.js)
```

**Stack.** FastAPI + SQLite on Cloud Run · 11 routes · 12-node HappyRobot workflow · single-file dashboard (no build step).

---

## Project structure

```
backend/
├── main.py            # FastAPI app + 11 route handlers
├── helpers.py         # Coercion, time windows, compute_ceiling()
├── models.py          # Pydantic models + 6 quality criteria + RATE_CEILING_PCT
├── audit.py           # Audit packing, flagged-queue logic, flag reasons
├── database.py        # SQLite schema + 15 seed loads
├── tests/             # Pytest suite — 31 tests
├── static/index.html  # Single-file dashboard
├── Dockerfile         # Honors Cloud Run's $PORT
├── deploy.sh          # Idempotent deploy to GCP
└── .env.example       # Required env vars

BUILD.md               # Customer-facing build description (the deliverable doc — for Acme Logistics)
ARCHITECTURE.md        # Engineering notes — stack, endpoints, data model, key decisions
README.md              # This file
```

---

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # set API_KEY (any string) + FMCSA_WEB_KEY
uvicorn main:app --port 8000 --reload
```

Then: dashboard at http://localhost:8000/dashboard, API docs at /docs, tests via `PYTHONPATH=. python -m pytest tests/ -v`.

---

## Deploy to Cloud Run

`deploy.sh` is idempotent — safe to re-run. It creates/reuses a GCP project, links billing, enables APIs, grants IAM, builds the container, and deploys with `--min-instances 1` (PoC SQLite constraint).

```bash
cd backend
bash deploy.sh                                 # first time
PROJECT_ID=happyrobot-fde-XXXX bash deploy.sh  # re-deploy
```

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `API_KEY` | yes | Header value clients must send as `x-api-key` for any data route |
| `FMCSA_WEB_KEY` | yes | FMCSA registry key for carrier verification |
| `RATE_CEILING_PCT` | no | Default ceiling as fraction of `loadboard_rate` (default `1.15` — broker pays up to 15% above posted). Per-load `maximum_rate` overrides this. |
| `RATE_ROUNDING_USD` | no | Round computed ceilings to nearest $X for natural speech (default `5`) |

---

## Security

- All data routes require `x-api-key` header authentication
- HTTPS enforced by Cloud Run (Google-managed cert)
- API key passed via env var, never committed; injected into dashboard HTML at request time
- Real FMCSA API integration with 10s timeout + graceful NOT_FOUND fallback

## Known PoC constraints

- SQLite on Cloud Run is ephemeral — data resets on redeploy. Production would migrate to Cloud SQL.
- `--max-instances 1` is a deliberate constraint to prevent SQLite split-brain on autoscale.
- Mock call transfer (per challenge spec — real warm-transfer requires a phone number, not a web call).
- Quality audit auto-grading uses our homegrown pipeline rather than HappyRobot's platform feature (which is internal-only).

---

For the customer-facing build description (call flow, dashboard story, margin discipline, audit criteria, what's tunable, what's next), see **[BUILD.md](./BUILD.md)**.

For engineering depth (stack, endpoints, data model, key decisions, reliability & security implementation), see **[ARCHITECTURE.md](./ARCHITECTURE.md)**.
