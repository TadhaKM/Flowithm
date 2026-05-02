# Engineering On-Call Runbook

_Last updated: 2026-03-12 by Tom Walsh_

## Overview

We run a primary + backup on-call rotation. Primary handles all P1/P2 alerts within 5 minutes of page. Backup steps in if primary is unreachable for 10 minutes. Rotation is one calendar week, Monday 9am Pacific to the following Monday.

Alerts page via PagerDuty. Current rotation members: Tom, Sam, Priya, Lisa, Jordan. New engineers shadow two full rotations before going primary.

## Severity definitions

- **SEV-1**: Production fully down, or active data loss / data integrity issue. Wake people up. Page CTO immediately.
- **SEV-2**: Significant degradation, customer-facing errors > 5%. Active business-hours response. Open a `#inc-YYYY-MM-DD` channel and declare in `#engineering-incidents`.
- **SEV-3**: Single feature broken, low overall impact. Fix during normal hours. No incident channel needed.

## Common incidents

### Database connection pool exhaustion

Symptoms: 503s on API endpoints, Datadog alert "RDS connection usage > 90%", logs containing `FATAL: remaining connection slots are reserved for non-replication superuser connections`.

Steps:
1. Open Datadog dashboard `api-prod-database` — confirm connection count and which API hosts are leaking
2. Identify recent deploys: `gh release list --limit 5`. Most pool issues come from a recent deploy.
3. If a recent deploy looks suspect, **roll back first, debug after**. We will lose 5 minutes of fix-forward to gain 10+ minutes of stable production.
4. Rollback command: `./scripts/deploy.sh rollback`
5. After rollback: post timeline in `#engineering-incidents`, request postmortem
6. Do **not** bump `max_connections` on RDS without engineering lead approval — it masks the leak and can destabilize the cluster

### Failed deploy

Symptoms: GitHub Actions deploy job fails, or deploy succeeds but ECS health check fails.

Steps:
1. Check the deploy log in GH Actions — if a migration failed, jump to "Migration failure" below
2. Roll back: `./scripts/deploy.sh rollback` (re-deploys previous tag)
3. If rollback also fails (rare), page CTO

### Migration failure

Steps:
1. Determine if migration was partially applied — query `schema_migrations`
2. If partial: do **not** roll back the deploy. Forward-fix with a corrective migration in a new PR.
3. If migration is fully un-applied: rolling back the deploy is safe.
4. Long-running migrations (>30s on prod-sized data) require a flagged-deploy plan — see Sam.

### Auth service down (Auth0)

Third-party. We cannot fix it.
1. Check status.auth0.com to confirm
2. Post template message in `#customer-success` for status communications
3. Update status page (status.loopline.com) — set to "Authentication degraded"
4. Wait. **Do not** enable our emergency password fallback path. It has not been audited since 2025-Q3 and is considered unsafe.

### Stripe webhook backlog

Symptoms: subscription state stale in app, Datadog alert "stripe-webhook-queue depth > 1000".

Steps:
1. Check BullMQ admin: `redis-admin.loopline.internal/queues`
2. Drain manually: `npm run drain-stripe-webhooks`
3. After drain: `npm run reconcile-stripe` to confirm `subscription_status` matches Stripe
4. If reconcile flags > 50 mismatches, escalate to Sam — that's a code bug, not a transient queue issue.

## Escalation paths

- Engineering: Tom → Sam → Jordan
- Security incidents (suspected breach, account takeover): Jordan → Maya immediately. Do not investigate alone.
- Customer-impacting incidents: also page Alex Morgan (CS lead) so support can communicate.
- Anything legal-adjacent (data breach, GDPR, subpoena): page Maya. Do not communicate externally before legal review.

---

# Refund & Cancellation Policy

_Last updated: 2026-04-16 by Alex Morgan_

This is the source of truth for how we handle cancellations, refunds, and credits. Public-facing policy lives at loopline.com/legal/refunds — keep this internal doc consistent with it, but **do not copy goodwill clauses into the public version**. Goodwill is intentionally case-by-case and not advertised.

## Plans

| Plan | Price | Term |
|---|---|---|
| Starter | $24/mo | Monthly |
| Team | $96/mo | Monthly or annual |
| Business | $240/mo | Monthly or annual |
| Business Plus | from $2,400/mo | Annual only, custom terms |

## Cancellation

Customers can cancel any time from billing settings. Cancellation takes effect at the end of the current billing period; service remains active until then. No cancellation penalty.

Annual plans: customer can cancel auto-renewal at any time, but the current annual term continues to its end date.

## Refunds

Three categories. Apply them in order:

### 1. 30-day money-back (automatic)

Within 30 days of first paid month, full refund on request. No questions, no documentation. Self-serve via support@loopline.com.

Does not apply to:
- Customers who already received a refund on a prior subscription
- Workspaces that exceeded 100 active users at any point during the trial

### 2. SLA breach credits (automatic, per contract)

Our SLA is 99.9% monthly uptime measured at the api.loopline.com edge. If we miss it in a calendar month, the customer is entitled to a credit:

| Monthly uptime | Credit |
|---|---|
| < 99.9% but ≥ 99.0% | 10% of monthly fee |
| < 99.0% but ≥ 95.0% | 25% of monthly fee |
| < 95.0% | 50% of monthly fee |

Credits are auto-applied to the next invoice. Customer does not need to request.

### 3. Goodwill credits (discretionary)

For incidents that don't breach SLA but materially impacted a customer (e.g., outage during their launch event, recurring minor issues, severe support failures).

| Credit amount | Approval |
|---|---|
| Up to 1 billing cycle | CS lead (Alex) |
| 1–3 billing cycles | CEO (Maya) |
| > 3 billing cycles or full refund of annual | CEO + co-founders |

Goodwill credits should be paired with qualitative gestures: a call from Maya or Jordan, a roadmap review, an early-access offer. The qualitative side often matters more than the dollar amount.

## What we do not refund

- Annual plans canceled mid-term: service runs to end-of-term. No pro-rated cash refund. May be converted to credit at CS lead discretion.
- Usage-based overages already invoiced.
- Sales tax (handled separately via Stripe Tax).

## Edge cases

**Customer claims they didn't use the product.** Check audit logs in admin panel. If genuine non-use within 30 days, apply 30-day money-back. If outside 30 days, default to half-cycle goodwill credit.

**Customer is moving to a competitor.** No refund obligation. Document reason in HubSpot for product feedback.

**Acquired customer wants to fold their subscription into the parent's.** Cancel the smaller, prorate-credit the parent. Loop in Maya for any deal over $20K ARR.

---

# How We Hire

_Last updated: 2026-02-08 by Emma Schultz_

We hire deliberately. Loopline is 12 people; one bad hire is meaningfully bad. We optimize for signal over speed. Target time-to-hire: 21 days from application to offer accepted.

## Stages

### 1. Application review (1–2 days)

Recruiter (Emma) reviews every application. We read the cover note. Generic AI-generated applications are screened out — we look for specifics about Loopline or the role.

- **Out**: applications that don't address the role specifics; people who don't list any concrete prior outcomes (numbers, scope, projects).
- **In**: anyone who can articulate why this role, here, now.

### 2. Recruiter screen (30 min, video)

Emma. Goals:
- Confirm location, comp expectations, start date
- Walk through their last role: scope, team, what they actually owned
- Loopline pitch and Q&A
- Calibrate: do their stated comp expectations fit our band?

- **Out**: misalignment on location/comp/timing.
- **In**: progress to role-specific screen.

### 3. Role screen (60 min, video)

- **Engineering**: take-home alternative or live coding (candidate's choice). 60-min real-world problem — extending a small TypeScript service. Reviewed against rubric (correctness, clarity, communication, technical depth). No algorithm puzzles. Hiring manager joins.
- **Customer Success / Sales**: scenario interview — we play a customer in a real situation, candidate handles it. Looking for empathy, structure, follow-through.
- **Design**: portfolio walk-through plus live critique of an actual Loopline screen.

### 4. Onsite (3–4 hours, video, same day)

Three back-to-back 60-min sessions:
1. Deep-dive with hiring manager — past projects, technical depth
2. Cross-functional collaboration — interviewer from a different function (e.g. eng candidate meets CS lead)
3. Values + scenarios with a founder (Maya or Jordan)

Plus a 30-min "ask us anything" with two random teammates. No interview, just real conversation.

### 5. References + offer (1–3 days)

We do back-channel references in addition to the candidate's provided list. We tell candidates we will, and ask if any back-channel would surprise them.

Offer: written, 7-day expiry by default (negotiable).

## Decision

Hiring manager + one co-founder must both be a clear "yes". We do not hire on tepid signal. If anyone on the panel is below "lean yes", we decline.

We've declined ~70% of candidates who reached onsite. That's intentional.

## What we look for (rubric)

- **Outcomes over tenure**: what did you actually ship, move, or change
- **Communication**: clear, specific, willing to disagree
- **Collaboration**: stories about hard interpersonal moments and how they handled them
- **Growth mindset**: actively learning, recent skill stretch, open to feedback
- **Values alignment**: candor, ownership, customer focus

## Comp philosophy

Top-of-market base for SF/NYC bands. Equity for everyone, no exceptions including Customer Success. Annual review aligned with calendar year.

We don't negotiate against offer letters from competitors. We make our best offer first.

## Diversity and sourcing

We track applicant funnel by demographic where disclosed. We will not hold a role open indefinitely for diversity reasons, but we will widen the source list and extend timelines if our pipeline is homogeneous.
