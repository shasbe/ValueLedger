# ValueLedger — POC Spec v0.2

**Status:** Draft for build · **Author:** Sudhir Hasbe (PM) · **Date:** 2026-09-26
**Context:** Enterprise PM take-home — "Paying for Intelligence"
*(v0.1 archived at `docs/SPEC-v0.1-archive.md`.)*

---

## 1. The problem and the wedge

Claude Enterprise bills consumption across Claude Code, Cowork, and chat. Every dollar is
fungible and **unlabeled**. Three questions go unanswered:

1. **Where did the money go?** Not "which seat" — *which business initiative.*
2. **What came out of it?** Not "tokens consumed" — *what shipped.*
3. **What will next quarter cost?** Not "trailing average" — *committed work vs. budget.*

The cause is not reporting. It is that **the atomic unit of attribution does not exist**.
No record says *"this $4.12 went to Payments Migration, doing refactor work, and produced
commit a3f91c which merged in PR #4412."* Chargeback, forecasting, ROI, and budget policy
are all blocked on that record not existing. Agents make it worse: autonomous consumption
has no human to vouch for it, so the share of spend with zero attributable intent grows
every quarter.

**The wedge: build the ledger.** ValueLedger creates the missing record — cost attributed
to business work, classified against the enterprise's own taxonomy, joined to verifiable
output. Everything else is downstream of it.

---

## 2. Customers

Three personas, one ledger, role-scoped server-side.

| Persona | Sees | Job to be done |
|---|---|---|
| **Initiative Owner** *(primary)* | their initiatives: burn vs. budget, breakdown by task type / activity / contributor, cost per merged PR, dark spend, review queue | "Defend or reallocate my initiative's budget at planning review" |
| **CFO / Finance** | everything aggregated: company total, by cost center and initiative, trend and forecast, attribution coverage, unattributed spend as an explicit line | "Approve next year's contract with a number I can defend" |
| **Individual** | their own sessions only, and can **correct their own classifications** | "Understand and correct how my work is counted" |

The individual view is non-negotiable. No product that measures people and hides the data
from them survives contact with an engineering org — and corrections are our highest-value
training signal.

---

## 3. What we claim, and what we refuse to

**We claim:** attributed cost, and verifiable output counts.
**We refuse:** a fabricated dollar value of work produced.

```
Payments Migration                     Q3 2026
  Attributed spend        $8,412   (94% of sessions classified)
  Merged PRs                  31
  Cost per merged PR        $271
  Dark spend               $1,514   (18% — sessions with no durable artifact)
  Rework spend               $602   (7% — work later reverted)
  Budget                  $12,000   →  70% consumed, 62% through period
```

No line is an assumption. **CFOs don't distrust the value of AI — they distrust invented
numbers about it.** The honest ratio is the only version that survives a second quarter.

### Three metrics only we can produce

They require cost and outcome joined at session grain, which nothing in the existing
FinOps or observability stack has:

- **Dark spend** — spend on sessions that produced no durable artifact.
- **Rework spend** — spend on work later reverted or superseded.
- **Attribution coverage** — the honesty meter, always reported as two numbers:
  *classification coverage* (% of work mapped to an initiative) and *cost coverage*
  (% whose dollar cost was **measured** rather than unknown). Code and Cowork score high
  on both; chat scores high on the first and zero on the second (§5.3).

---

## 4. POC scope

**In:** batch collector for Code + Cowork · MCP server · attribution skill for chat ·
model-mediated classification against a server-held policy · ledger · outcome joins ·
three role-scoped views · deployed on GCP.

**Out, each for a stated reason:**

| Deferred | Why |
|---|---|
| Enterprise hook | Collector reads what a hook would send (§5.1). Hook is for fleet coverage, real-time, and the trust boundary (§5.5) — none needed to prove the thesis. |
| Server-side classification | Requires prompts to leave the laptop for a third-party service. Wrong trade here; belongs behind an enterprise hook (§6.3). |
| Reclassification of history | Follows from local classification. `/v1/reclassify` is v2. |
| Real-time budget enforcement | Impossible without pre-flight cost estimation (§8.4). POC is a detective control and says so. |

---

## 5. Collection

**Verified on a real machine, 2026-09-26.** Claude Code and Cowork already persist
everything needed. The collector reads it rather than intercepting it.

### 5.1 What is on disk

`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — CLI, desktop Code, **and Cowork**
(under `scratch-workspaces/`) all write the same format:

- **Typed user prompts** — `type=="user"` where `message.content` is a **string**. Tool
  results are arrays; that is the separator. (`promptSource` is mostly null and unusable.)
- **Per-request usage** on `type=="assistant"` — `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation` split by 5m/1h TTL, `model`, `timestamp`.
- **Context** — `cwd`, `gitBranch`, `sessionId`, `isSidechain` (→ subagent attribution).
- **Summaries** — `ai-title` / `last-prompt`, present in ~half of sessions. A classifier
  hint, never a dependency.

Ignore `history.jsonl` (a 9-line UI buffer) and `stats-cache.json` (stale, partial,
`costUSD: 0`). `~/.claude/roiclaude/spend.db` is a working reference implementation of
the pricing logic — 2093 rows / 20 sessions / $409.78 observed.

### 5.2 Two scheduled tasks, one implementation

Same code path, different watermark bound. Build once, schedule twice.

| Task | Bound | Cadence | Purpose |
|---|---|---|---|
| `collect --full` | epoch (or `--since`) | once at install, on demand | bootstrap + recovery |
| `collect` | last successful watermark | daily | steady state |

Per run: enumerate transcripts modified since the bound → extract prompts, roll up usage
per session per model, capture git context → capture outcomes (commits in window, files
changed, lines ±, test files touched) → classify (§6) → publish → advance watermark on
success only. Dedupe on `session_id` + record uuid, so **every run is idempotent**.

**The daily run is self-healing.** It scans *"modified since watermark"*, not *"modified
yesterday"*, so a laptop asleep for a week catches up on its next tick unaided. A gap only
becomes unrecoverable if it exceeds retention (§5.4).

**`--dry-run` is mandatory on full runs.** A backfill could classify hundreds of sessions
at once — a cost spike from the cost-control tool. It reports *"will classify 247
sessions, est. $2.10"* and spends nothing until confirmed. A tool that meters its own
backfill demonstrates the thesis better than any dashboard tile.

**Backfill is a commercial property, not just a convenience.** A design partner installs
Monday and sees ~30 days of populated dashboard that afternoon, so the week-6 PMF signal
(§9) can fire in week 1.

**Scheduling:** Claude Code's `~/.claude/scheduled-tasks/`. Zero OS setup, cross-platform,
and Claude scheduling the job that measures Claude is a pleasing symmetry. Logic lives in
a plain CLI, so launchd/cron/MDM can drive it unchanged.

### 5.3 Chat is the exception

Chat runs server-side; nothing lands locally. It is reachable only through the MCP skill
(§7.2), which yields **labels with no measurable cost** — the model cannot see its own
token usage.

| Surface | Path | `cost_basis` |
|---|---|---|
| Claude Code (CLI + desktop) | batch collector | `measured` |
| **Cowork** | batch collector | `measured` |
| **Chat** | MCP skill | `unknown` |

This is why coverage is always reported as two numbers. *"We can classify 92% of your chat
work and price 0% of it"* is a more useful artifact for a CFO than a confident wrong
number — and it is the evidence for the platform ask in §8.2.

### 5.4 Retention is a hard constraint

Claude prunes `projects/` on a cleanup cycle (observed ~36 days, governed by
`cleanupPeriodDays` — read it, don't assume it). **Transcripts past the window are gone
permanently.** So: daily cadence well inside the window; alert when a machine's gap
*approaches* retention; `--full` runs immediately at install.

### 5.5 Privacy

The POC ships **`local` mode only**: classification runs on-device, and only labels,
confidences, and rationales leave the machine. No prompt text ever leaves a developer's
laptop. That removes the largest deployment objection, at the cost of central classifier
improvement and reclassification (§6.3).

---

## 6. Classification

**Every session is classified by an LLM, retrospectively, once per session.** There is no
deterministic classification path — the collector is deterministic about *cost*, never
about *meaning*.

Retrospective is a design upgrade, not a compromise: a hook-time classifier sees the first
prompt and guesses where the session is going, while the batch job sees the **entire
session**. Strictly more accurate, and one call per session instead of one per prompt.

### 6.1 The Attribution Policy is a versioned prompt bundle

The service is the single source of truth for **how to classify**, not just for the list
of labels. Every dimension carries its own prompt text.

```jsonc
{
  "policy_version": 14,
  "global_guidance": "Classify the work in this session. ...",
  "initiatives": [
    { "key": "payments-migration", "name": "Payments Migration",
      "classification_guidance":
        "The Stripe→Adyen cutover. Includes payments-svc and billing-gateway,
         webhook handling, reconciliation jobs. Does NOT include general
         billing UI work — that is Billing UX." }
  ],
  "task_types": [ /* same shape */ ],
  "activities": [ /* same shape */ ],
  "examples": [ { "dimension": "initiative",
                  "example_text": "fix idempotency key on refund retry",
                  "label": "payments-migration" } ]
}
```

- **Admins tune fleet-wide classification by editing text in one place** — no client
  release. Classification quality becomes a content problem, which is the kind an
  initiative owner can fix themselves.
- **The correction flywheel closes.** Corrections are promoted into `examples` and ship to
  every client on next fetch. The system improves from use, per org.

### 6.2 Flow

1. Task calls **`get_attribution_policy()`** over MCP. Cached with a TTL; on fetch failure
   it uses the last good bundle and flags records as classified against a stale version.
2. Classifier assembles **all** typed prompts in order, repo, branch, files touched,
   `ai-title`/`last-prompt` if present, and the policy bundle.
3. A cheap fast model returns structured JSON: three labels, a confidence each, and a
   one-sentence rationale.
4. **`unclassifiable` is a first-class answer.** If nothing fits, the classifier says so
   rather than forcing a label. These cluster in the admin view as *"work your taxonomy
   does not describe"* — which is how initiatives get **discovered** rather than assumed.
5. `conf < 0.7` on any dimension → `needs_review`, into the owner's queue.
6. Published via **`publish_attribution()`**; corrections promoted into `policy_examples`.

### 6.3 It runs on the machine

Policy is always fetched from the service; the model call happens locally. Server-side
would be better engineering — central logs, one classifier version, free reclassification
— but requires every developer's prompts to leave their laptop for a third-party service.
That is an unnecessary trade, since the signal reduces to labels before crossing the
boundary.

**This is what an enterprise hook really buys.** A managed hook runs inside the customer's
own control plane, so prompts reaching the service become an *intra-enterprise* transfer
governed by the same policy that already governs Claude Enterprise — categorically
different from a laptop posting prompts outward.

**Accepted consequences:** no reclassification; the review queue shows the rationale and
confidence, **not the prompt** (users can see their own prompts locally and correct the
record, so disputes resolve — just not from the reviewer's side alone); fleet drift is
unmeasured but stamped via `policy_version` + `classifier_version` so it becomes
measurable later.

### 6.4 Cost discipline

A cost-attribution product that adds meaningful recurring inference cost is
self-defeating. Three controls, all visible in the product:

- **Self-metering.** `classifier_cost_usd` per classification, surfaced as its own line:
  *"Classification overhead: $31 (0.37% of tracked spend)."* The obvious objection becomes
  a proof point.
- **Session grain.** One call per session, not per prompt — the largest single reduction.
- **Prompt-hash cache.** Identical prompt sets on the same repo+branch reuse the result.

Every classification stores rationale, confidence, `policy_version`, and
`classifier_version`. An unexplainable chargeback is one that gets disputed, then ignored.

---

## 7. Interfaces

### 7.1 Data model

```sql
orgs(id, name, api_key_hash, created_at)

users(id, org_id, email, display_name, cost_center, manager_email,
      role)                              -- member | initiative_owner | finance | admin

attribution_policies(id, org_id, version, published_at, published_by,
                     checksum, global_guidance)

initiatives(id, org_id, policy_version, key, name, description,
            classification_guidance, owner_email,
            budget_amount, budget_period, cost_center, status)
task_types(id, org_id, policy_version, key, name, description, classification_guidance)
activities(id, org_id, policy_version, key, name, description, classification_guidance)
policy_examples(id, org_id, policy_version, dimension, example_text,
                label, source_correction_id)

sessions(id, org_id, session_id UNIQUE, surface, user_email,
         principal_type, parent_session_id,          -- agentic attribution
         repo, branch, cwd_hash, started_at, ended_at, client_version,
         collection_method,               -- collector | mcp_skill
         cost_basis,                      -- measured | unknown
         external_ref)

usage_events(id, session_id, ts, model, request_id,
             input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens,
             cost_usd, price_book_version)

classifications(id, session_id, initiative_id, task_type_id, activity_id,
                conf_initiative, conf_task_type, conf_activity,
                rationale, classifier_model, classifier_cost_usd,
                classifier_version, policy_version,
                status,                  -- auto | needs_review | confirmed
                                         -- | corrected | unclassifiable
                created_at)
classification_corrections(id, classification_id, corrected_by,
                           prev_json, new_json, note, ts)

outcome_events(id, org_id, session_id, type, ref, ts, metadata_json)
             -- commit | pr_opened | pr_merged | pr_closed | test_added
             -- | revert | file_changed

price_book(model, effective_from, input_per_mtok, output_per_mtok,
           cache_read_per_mtok, cache_write_per_mtok)
budgets(id, org_id, scope_type, scope_id, period, amount_usd)
```

Raw token counts are stored alongside computed cost so the ledger is **recomputable** when
the price book changes. Client-reconstructed cost will drift from the invoice;
recomputability keeps that drift auditable rather than mysterious (§8.1).

`principal_type` and `parent_session_id` exist now even though POC traffic is mostly
human — agentic spend should have a shape in the ledger before it dominates (§8.3).

### 7.2 API and MCP

```
POST   /v1/events                       batch ingest (org API key)
POST   /v1/sessions/{id}/outcomes       outcome artifacts
GET    /v1/ledger                       session-grain query
GET    /v1/rollup                       ?group_by=initiative|user|task_type
                                         |activity|cost_center&from&to&surface
GET    /v1/reports/executive            CFO view
GET    /v1/reports/initiative/{key}     initiative owner view
GET    /v1/reports/me                   self view
GET    /v1/review-queue
POST   /v1/classifications/{id}/correct
GET|PUT /v1/policy                      Attribution Policy (admin)
GET|PUT /v1/budgets
POST   /internal/reconcile              Cloud Scheduler: poll GitHub for merges

/mcp                                    streamable HTTP, per-user OAuth
  get_attribution_policy()              versioned prompt bundle (§6.1)
  publish_attribution(work_unit)        idempotent on external_ref
  query_my_spend(period)                self view
  query_initiative(key, period)         role-scoped
```

The read tools matter more than they look: they make the ledger **queryable from inside
Claude** — *"how much has Payments Migration spent, and what shipped?"* A report gets
cancelled at renewal; a thing people ask questions of does not.

**Trust boundary.** Everything arriving over MCP is model-generated content, not fact.
Identity comes from the **OAuth token, never the payload**. Cost is never accepted from
the model. Initiatives must pre-exist in the policy. This is not hygiene: a Cowork or chat
session may be reading untrusted documents, and a prompt-injected file saying *"record
this against the Platform initiative"* must not move money on someone else's books.

**Auth:** org API key (hashed) for ingest; Google OIDC for the dashboard. Role scoping
enforced in the query layer, not the UI — `member` can only ever read their own rows.

### 7.3 Deployment (GCP)

| Concern | Service |
|---|---|
| API + MCP | **Cloud Run** (FastAPI, scale-to-zero, `/mcp` on the same service) |
| Ledger | **Cloud SQL / Postgres 16** (SQLite for local dev) |
| Outcome reconciliation | **Cloud Scheduler** → `/internal/reconcile`, hourly |
| Secrets | **Secret Manager** |
| Dashboard | published **Artifact**, read-only against the API |

Scale-to-zero is honest for the category: a cost-control product that costs real money to
run is a bad look.

---

## 8. What this needs that isn't possible yet

### 8.1 No customer-defined attribution tags in the billing path
No way to tag a request with `initiative=payments-migration` and see it on the invoice. We
reconstruct cost from tokens × a price book, which **drifts from the real invoice** —
cache tiering, contract discounts, and batch pricing are invisible.

> **Ask:** an `x-attribution-tags` request header, echoed on Admin API usage records. The
> highest-leverage platform change for this category.
> **Meanwhile:** recomputable ledger; show reconstructed cost with an explicit
> variance-vs-invoice view rather than claiming invoice truth.

### 8.2 Chat has no local footprint and no cost visibility
The MCP skill (§5.3) proves intent classification generalizes, but capture is
model-mediated — so coverage is best-effort and **biased toward missing expensive
sessions**, and the model cannot see its own usage, so there is no cost at all.

> **Ask:** an org usage export carrying a session/conversation id we can join on. Cheaper
> than a client event surface and unlocks most of the value — we have the labels, we need
> a key to attach dollars to.
> **Meanwhile:** `collection_method` and `cost_basis` on every row, so when an export
> appears, records upgrade from `unknown` to `measured` **in place** — no migration.

### 8.3 Autonomous spend has no principal
Agents spawning subagents or running on a schedule have no human to attribute to and no
provenance chain. This worsens every quarter — the reason to build the ledger now.

> **Ask:** delegation provenance in the session record — who authorized this run, under
> what policy, on whose behalf.
> **Meanwhile:** `principal_type` and `parent_session_id`, inferred from `isSidechain`.
> Imperfect, but agentic spend gets a shape instead of being an unlabeled blob.

### 8.4 No pre-flight cost estimation
You cannot know what a task will cost before running it, so budgets can only be
*observed*, never *enforced*.

> **Ask:** a cost-estimation endpoint, or budget-aware stop conditions.
> **Meanwhile:** alerting and forecast burndown. The POC is a detective control and does
> not pretend otherwise.

**Strategy across all four:** build client-side and provider-neutral now, publish the
attribution schema as an open spec, and use design-partner evidence as the forcing
function for native support. When native tags land we swap the reconstruction layer and
nothing above `/v1/events` changes — the ledger, taxonomy, outcome joins, and correction
flywheel are the durable assets.

---

## 9. Validation and path to PMF

**Design partners:** 5–8 enterprises with >$250k annual Claude Enterprise spend and a
platform team that can deploy a scheduled task.

| When | Signal | Threshold | A miss means |
|---|---|---|---|
| Week 1–2 | **Install rate** | live on >50 devs | If they won't deploy it, the pain isn't real. Kill or re-wedge. |
| Week 2–3 | **Classification acceptance** | >85% uncorrected | Taxonomy or policy prompts are wrong — fixable. |
| Week 4 | **Decision change** ⭐ | ≥1 owner reallocates, kills, or expands based on the dashboard | The real PMF signal. Backfill (§5.2) makes week 4 realistic. |
| Quarter | **Budget artifact** | data appears in a budget or renewal doc | Commercial validation. |

**Anti-signal:** dashboard opened once at setup, never again. That means we built a
report, not a workflow — and reports get cancelled at renewal.

**PMF definition:** *ValueLedger data is cited in the customer's quarterly budget defense.*
Not logins, not sessions ingested. Cited where the money is decided.

**Six months:** ledger (now) → budgets & alerts → enterprise hook, unlocking fleet
coverage and server-side classification → agentic attribution → **benchmarks**:
anonymized cross-customer medians, *"your cost per merged PR is 2.1× the p50."* The
benchmark layer is the compounding moat — it requires N customers and cannot be
bootstrapped by a competitor or built in-house.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| **Surveillance perception** kills adoption | Self-view always on; users correct their own data; `local` mode; aggregate to initiative, never rank individuals |
| **Transcript retention** — a gap past the window loses data permanently | Daily runs self-heal within the window; alert as a machine's gap approaches retention; `--full` at install |
| **Backfill cost spike** | `--dry-run` preview required; `--since` to bound scope |
| **Classifier cost** (accepted trade-off) | Self-metering surfaced in-product, session grain, prompt-hash cache |
| **Fleet classification drift** | Stamp `policy_version` + `classifier_version`; sample-audit agreement |
| **Cost drift vs. invoice** | Recomputable ledger, explicit reconciliation view, never claim invoice truth |
| **Dark spend is gameable** | Report as a trend against a defined artifact threshold; treat gaming as a signal to talk to that team |
| **Skill-path coverage is biased**, not merely incomplete | Never extrapolate skill coverage to a total; report paths separately |
| **Prompt injection via MCP** | Identity from OAuth token not payload; cost never model-supplied; initiatives must pre-exist; rate limits; user-visible corrections |
| **Anthropic ships this natively** | Likely, and fine — durable assets are the taxonomy, outcome joins, correction flywheel, and benchmarks. Provider-neutral by design. |

---

## 11. Build order

1. **Service skeleton** — FastAPI, schema, migrations, SQLite local / Postgres on GCP
2. **`/v1/events` ingest** + price book + cost computation
3. **Seed generator** — synthetic 90-day org: 40 users, 6 initiatives, 4 cost centers
4. **Rollup + report endpoints** with role scoping
5. **MCP server** — `/mcp`, four tools, OAuth identity, idempotent publish
6. **Classifier** — policy bundle, structured output, confidence, `unclassifiable`,
   self-metering
7. **Collector CLI** — transcript parse (Code + Cowork), usage rollup, git outcomes,
   watermark; `--full` with `--dry-run`, plus daily incremental; two scheduled tasks
8. **Attribution skill** — classify-and-publish for chat
9. **Dashboard** — three role-scoped views, published as an Artifact
10. **Deploy** — Cloud Run + Cloud SQL + Scheduler reconciler
11. **README** — run locally in one command, deploy in one command

Steps 1–4 are the service and are built first: it is the contract every other component is
a client of, and the expensive thing to change once there is data in the ledger. Seed data
lands at step 3 so the dashboard is demoable before the collector exists.
