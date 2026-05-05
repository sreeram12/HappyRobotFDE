# Carrier Sales Automation — Build Description for Acme Logistics

**Prepared for:** Acme Logistics ops & sales leadership
**Prepared by:** Sreeram Sandrapati
**Platform:** HappyRobot · **Status:** Demo-ready, deployed

> **TL;DR.** A voice AI agent answers your inbound carrier calls 24/7, verifies the carrier with FMCSA, negotiates inside a per-load pay ceiling you control, and hands off on agreement. Every call is auto-graded against six behavioral criteria; failures route to a supervisor queue. The agent never reveals our number before learning the carrier's. The dashboard is built for the new question your supervisor will ask: *"did the AI do the right thing?"*

---

## What we built and why it matters

Inbound carrier calls are the highest-volume, lowest-complexity sales activity in your business. Every call follows the same pattern — verify the carrier, pitch the load, negotiate the rate, hand off the booking. Today, those calls are handled by your reps during business hours and missed when they're not. That's a coverage problem and a margin-discipline problem at once.

We built an AI agent on HappyRobot that handles inbound carrier calls end-to-end: 24/7, with FMCSA verification baked in, with margin guardrails the AI cannot violate, and with every call captured into a supervisor dashboard you can act on Monday morning.

The system is **live** as of this writing. It has handled test calls, logged them, audited them against six quality criteria, and surfaced the right ones for human review.

---

## What this changes for Acme

| Before | After |
|---|---|
| Reps spend 10–15 min/call on repetitive vetting + pitch + negotiation | AI handles the full flow in 2–3 min/call |
| After-hours calls go to voicemail or get missed | 24/7 answer rate with no headcount |
| Margin ceiling depends on which rep is on the phone | Ceiling is a number in the system — non-negotiable, configurable per load |
| Call quality is audited by spot-checking a few recordings | Every call is auto-graded against six criteria; failures route to the supervisor's queue |
| Outcome data lives in rep notebooks and CRM free-text fields | Every call lands as structured data in a supervisor dashboard within seconds |

---

## How a call works (carrier's perspective)

1. **They dial in.** Paul (the AI agent) answers and asks how he can help.
2. **They mention a load.** Paul collects either a reference number or — if they don't have one — the lane and trailer type.
3. **He gets their MC number** and verifies live against the FMCSA registry. Inactive carriers are politely declined and the call ends. Active carriers get their company name read back for confirmation — catches MC typos and identity issues before any load details are shared.
4. **He pitches the load** in natural language: lane, pickup/delivery, commodity, weight, special notes — but **does not quote a number yet**.
5. **He asks the carrier their target rate first.** Real freight reps do this — it learns the carrier's number before tipping the broker's hand.
6. **They negotiate, or they don't.** Paul applies the schedule + tit-for-tat (see "Margin discipline" below). Paul will only concede when the carrier moves first.
7. **On agreement,** Paul says: *"Let me get you transferred to my colleague to finalize the paperwork. Transfer was successful — you can wrap up the conversation."* (Real warm-transfer is a deployment-time integration with your phone system; the demo uses a mock message.)

The conversation is calibrated to feel natural — Paul uses filler words, reads numbers back clearly, confirms before acting. He won't sound robotic.

---

## How this looks for your supervisor

Your ops manager opens the dashboard and sees, in this order:

1. **Revenue Booked this week** — the giant number at the top.
2. **Walked-Away Value** — the total *ceiling* value of loads where the AI held our pay limit and the carrier walked. A discipline metric, not a loss metric. Paying more would have crossed our ceiling.
3. **Rate Capture, Booking Rate, Calls Handled** — supporting KPIs.
4. **After-Hours Calls** — calls that came in before 8 AM or after 6 PM. Pure coverage gain.
5. **Needs Your Review** — a queue of 3-5 calls that need supervisor attention. Each row shows carrier, MC, lane, outcome, and a one-line reason. **No supervisor scans 200 calls to find the ones that matter.**
6. **Top Lanes by Revenue** — which routes are working.
7. **Agent Performance** — call volume + revenue trend, hour-of-day distribution, sentiment, negotiation depth.
8. **Quality Audit** — collapsed by default; expand to see per-criterion pass rates.
9. **Recent Calls** — the full filterable log.

The dashboard is designed for the new question: **"Did the AI do the right thing?"** Not the old: "Did this rep close enough deals?" Different mental model, different surfaces.

A call shows up in the **Needs Your Review** queue for one of three reasons:
- **Operational failure** — the call ended without a booking (carrier ineligible, no matching load, walked on rate, or hung up)
- **Compliance failure** — one of the six quality criteria graded "fail" (red rail; outcome flags get amber)
- **Negative carrier sentiment** — even on booked calls

A team handling 200 inbound calls/day might see 8-12 flagged. That's tractable human attention focused on the right cases.

---

## Margin discipline

The agent's negotiation behavior is built around four principles:

1. **Ask before quoting.** The agent asks the carrier their target rate *before* revealing any number. If the carrier asks for less than we'd offer, we accept silently and pocket the difference.
2. **Separate policy from behavior.** The pay-ceiling lives in your loads database, configurable per-load. The behavior (how to negotiate up to it) lives in the agent's instructions. The agent does only *comparison* and *rounding* — never percentage math live on the call.
3. **Tit-for-tat concession.** The broker only concedes when the carrier moves toward it. A carrier who holds firm doesn't get a free step. Tit-for-Tat is the highest-performing strategy in iterated bilateral bargaining.
4. **Walk-away pressure.** Each concession step is paired with verbal framing — *"that's pretty close to my ceiling," "I'll have to move on."* Mimics how skilled reps create urgency without further concession.

### How the negotiation flows

The posted rate is the **list price** you'd prefer to pay. The ceiling is the **maximum** you'll pay before walking. The schedule walks UP from list to ceiling.

For a $2,100 load with a $2,415 ceiling (115% default), the schedule is **[$2,100, $2,250, $2,415]**:

| Carrier asks | Broker responds |
|---|---|
| **Below list** ("I'll do $1,800") | **Accept immediately.** Carrier asked for less than we'd offer — book at their number. |
| **At list** | Accept. |
| **Above list** ("$2,400") | Counter at list (step 1). Then apply tit-for-tat. |

**Tit-for-tat on subsequent rounds:**
- Carrier dropped their ask by **≥$50** since last round → broker steps UP to next schedule level
- Carrier dropped by **<$50** (held firm) → broker HOLDS, says *"I've already stretched as far as I can"*
- Carrier raised their ask → broker HOLDS, calls it out
- Carrier accepts at-or-below broker's current offer → deal at carrier's number

After step 3 (ceiling) and carrier still won't accept → broker walks gracefully with *"got other carriers waiting."*

### Why this matters

> AI agents can be unreliable at on-the-fly percentage math under real-time voice. Quoting "$2,420 because that's 15% above $2,100" — when the actual answer is $2,415 — is the kind of $5 leak that compounds across thousands of calls. Pre-computing the schedule in your data means the AI never has to do live math. That's the production-grade pattern.

> Asking the carrier first is the more important behavior. Real freight reps learn the carrier's number before showing theirs. The AI does the same.

### Seed-data examples

- **DRY001** (Chicago → Atlanta, $2,100) — default 115% ceiling: **$2,415**
- **REF002** (Fresno → Seattle, $4,200) — illustrative 120% override: **$5,040**
- **FLT001** (Detroit → St. Louis, $2,400) — illustrative 108% override: **$2,590**

> **A note on these illustrative values.** The 120% / 108% on REF002 and FLT001 are placeholder policies chosen to demonstrate that two loads in the same database can carry different ceilings — not a recommendation for what Acme's actual policy should be. Real values come from your shipper-side rates, lane density, equipment scarcity by season, and carrier-tier policy. Setting your real ceilings is a 30-minute discovery conversation per load class, or we can derive defaults from your historical booking data.

---

## Quality assurance — six criteria, every call

After every call, the system reads the transcript and grades the agent against six criteria — pass / fail / not-applicable plus a one-line reason:

| Criterion | Priority | What it checks |
|---|---|---|
| Carrier verification compliance | High | Did the agent verify MC with FMCSA before sharing load details? |
| Asks for carrier's target rate first | High | Did the agent get the carrier's number before quoting any of ours? |
| Rate discipline | High | Did the agent ever quote ABOVE the load's ceiling? |
| Identity confirmation | High | Did the agent read the carrier name back and get confirmation? |
| Negotiation cap adherence | Medium | Did the agent end at round 3 or earlier? |
| Handoff completion | Medium | On agreement, did the agent play the transfer message? |

These aren't generic politeness checks — they're load-bearing **policy enforcement**. A failure on "Rate discipline" means the AI went above your ceiling, which is a margin event. A failure on "Carrier verification" means proprietary load data was shared with an unauthenticated carrier. Both auto-flag for supervisor review.

The platform has a native quality-grading feature (Northstars); your account doesn't have it enabled (it's an internal HappyRobot capability), so we built our own grading directly into the workflow. When the native feature flips on for your org, the same six criteria transfer cleanly — same names, same descriptions, same priorities.

---

## What's tunable for your business

No engineering required:
- **Per-load ceiling.** Set in your loads database. Overrides the default percentage.
- **Default ceiling percentage.** A single central setting (default 115%).
- **Negotiation rounds.** Currently 3 (per the challenge spec). Adjustable in the agent instructions.
- **Voice + persona.** Switchable in HappyRobot's voice library. We use "Paul" as a placeholder.
- **Domain key terms.** Words the agent listens for (load IDs, freight jargon). Already configured.

Discovery conversation:
- **Carrier-tier policies.** Different ceilings for preferred vs. new carriers.
- **Lane- or load-class-specific scripts.** Hazmat / oversize qualifying questions.
- **TMS integration.** Booking decisions write back to your existing system (~2-4 weeks for a real engagement).
- **Warm transfer.** Real handoff (not the mock message) requires a phone number purchase + SIP trunk routing.

---

## Reliability and security

- **Always-on hosting with automatic HTTPS** — your team's connection to the dashboard is encrypted end to end.
- **API access is authenticated** — only authorized clients can read or write call data. Credentials are stored securely, never in source code.
- **FMCSA verification has a fallback** — if the registry is briefly unreachable, the agent declines politely rather than crashing mid-call.
- **Duplicate-safe call logging** — if the platform retries a call's data, we don't create double records.
- **Validated data at every boundary** — common edge cases (empty values, oddly formatted numbers) are handled gracefully.
- **Automated tests** covering the rate logic, audit grading, and exception handling.

PoC-stage limitations a real engagement would address: a production-grade database for long-term call history and high concurrency; per-client throughput limits; richer audit logging tied to specific call IDs; a backup path so no call data is ever lost in transit.

---

## What's next

For a real engagement, the next 30 days would look like:

- **Week 1 — Discovery + data.** Map your real load data (rate cards, lane economics, carrier tiers). Set per-lane / per-carrier ceiling policies. FMCSA web key on your account.
- **Week 2 — Workflow customization.** Tune the agent's instructions for your tone, lanes, and call patterns. Equipment-specific qualifying questions. Read-only TMS integration so the agent has live load inventory.
- **Week 3 — Closed beta.** Route 10-20% of inbound calls to the AI. Daily review of flagged calls. A/B test concession strategies.
- **Week 4 — Wider rollout.** 50%+ of inbound traffic. TMS write-back. SIP trunk for real warm transfer.

The system as built is the foundation. Everything above is tuning, integration, and trust-building — not rebuilding.

---

## Links

- **Live dashboard:** URL provided in the submission email (kept out of the public repo to avoid public indexing)
- **Code repository:** [link to be added once pushed]
- **HappyRobot workflow:** [https://platform.happyrobot.ai/fdesreeramsandrapati/workflows/qi4lcsb4k3zj/editor/ngloab75iopl](https://platform.happyrobot.ai/fdesreeramsandrapati/workflows/qi4lcsb4k3zj/editor/ngloab75iopl) (FDE Assessment, dev environment — HappyRobot login required)
- **Walkthrough video:** [link to be added]

**Questions or follow-ups:** Sreeram Sandrapati · ssandrapati477@gmail.com
