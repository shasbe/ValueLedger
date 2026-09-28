# ValueLedger — POC Build Spec v0.3

**Status:** Draft for build · **Author:** Sudhir Hasbe · **Date:** 2026-09-26

Build scope only. Strategy, validation, PMF, and platform asks live in a separate doc.
Earlier versions retained at `docs/SPEC-v0.1-archive.md` and `docs/SPEC-v0.2-archive.md`.

---

## 1. What we are building

Claude Enterprise bills consumption across Claude Code, Cowork, and chat, and every
dollar is unlabeled. ValueLedger creates the missing record: **cost attributed to
business work** — classified against the enterprise's own taxonomy, joined to verifiable
output.

Four components:

| Component | Role |
|---|---|
| **AttributionService** | ledger, policy store, reports. Cloud Run + Postgres |
| **Collector** | scheduled local CLI; reads Claude Code + Cowork transcripts |
| **MCP server** | policy distribution, attribution publish, ledger queries |
| **Attribution skill** | chat surface, where no local transcript exists |
| **Dashboard** | three role-scoped views, published Artifact |

---

## 2. Roles and views

Three roles, one ledger, scoping enforced server-side in the query layer — never in the
UI. A `member` can only ever read rows where `user_email` is their own.

| Role | Sees |
|---|---|
| `member` | own sessions only; can correct own classifications |
| `initiative_owner` | own initiatives: burn vs. budget, breakdown by task type / activity / contributor, cost per merged PR, dark spend, review queue |
| `finance` | everything aggregated: company total, by cost center and initiative, trend, attribution coverage, unattributed spend as an explicit line |
| `admin` | policy, budgets, org settings, API keys |

The `member` self-view ships in v1, not later. Corrections from users are the highest-value
input to the policy examples (§5.1).

---

## 3. Metrics to implement

The product reports attributed cost and verifiable output counts, plus **one modelled
metric — productivity gain (§6)** — which is structurally separated from everything else
and always rendered with its assumptions attached.

**It never computes a dollar value of work produced.** Hours saved is reported; hours
saved × a loaded rate is not, because that stacks a second assumption on the first and is
where this class of metric stops being credible.

```
Payments Migration                     Q3 2026
  Attributed spend        $8,412   (94% of sessions classified)
  Merged PRs                  31
  Cost per merged PR        $271
  Dark spend               $1,514   (18% — sessions with no durable artifact)
  Rework spend               $602   (7% — work later reverted)
  Budget                  $12,000   →  70% consumed, 62% through period
```

Definitions:

- **Dark spend** — spend on sessions with zero `outcome_events` above a configured
  artifact threshold.
- **Rework spend** — spend on sessions whose changed files were reverted or superseded
  within N days.
- **Attribution coverage** — always **two numbers, never collapsed into one**:
  *classification coverage* (% of work mapped to an initiative) and *cost coverage*
  (% whose cost is `measured` rather than `unknown`).
- **Classification overhead** — `SUM(classifier_cost_usd)` as a % of tracked spend,
  surfaced as its own line.
- **Productivity gain** — see §6. Measured numerator, assumed denominator, never merged.

---

## 4. Collection

**Verified on a real machine, 2026-09-26.**

### 4.1 What is on disk

`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` — CLI, desktop Code, **and Cowork**
(under `scratch-workspaces/`) all write the same format:

- **Typed user prompts** — `type=="user"` where `message.content` is a **string**. Tool
  results are arrays; that is the separator. `promptSource` is mostly null and unusable.
- **Per-request usage** on `type=="assistant"` — `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation` split by 5m/1h TTL, `model`, `timestamp`.
- **Context** — `cwd`, `gitBranch`, `sessionId`, `isSidechain` (→ `parent_session_id`).
- **Summaries** — `ai-title` / `last-prompt`, present in ~half of sessions. A classifier
  hint, never a dependency.

Ignore `history.jsonl` (a 9-line UI buffer) and `stats-cache.json` (stale, partial,
`costUSD: 0`). `~/.claude/roiclaude/spend.db` is a working reference for the pricing
logic — 2093 rows / 20 sessions / $409.78 observed.

### 4.2 Surface coverage

| Surface | Path | `cost_basis` |
|---|---|---|
| Claude Code (CLI + desktop) | batch collector | `measured` |
| Cowork | batch collector | `measured` |
| Chat | MCP skill (§6) | `unknown` |

Chat runs server-side with no local footprint, and the model cannot see its own token
usage — so the skill yields labels with **no cost**. This is why coverage is two numbers.

### 4.3 Two scheduled tasks, one implementation

Same code path, different watermark bound.

| Task | Bound | Cadence | Purpose |
|---|---|---|---|
| `collect --full` | epoch (or `--since`) | once at install, on demand | bootstrap + recovery |
| `collect` | last successful watermark | daily | steady state |

Per run:

1. Enumerate transcripts modified since the bound.
2. Extract typed prompts; roll up usage per session per model; capture git context.
3. Capture outcomes: commits in the session window, files changed, lines ±, test files
   touched.
4. Classify (§5).
5. Publish; advance watermark **on success only**.

Dedupe on `session_id` + record uuid — **every run is idempotent**.

**The daily run is self-healing.** It scans *"modified since watermark"*, not *"modified
yesterday"*, so a laptop asleep for a week catches up unaided on its next tick.

**`--dry-run` is required on full runs.** A backfill could classify hundreds of sessions
at once. It reports *"will classify 247 sessions, est. $2.10"* and spends nothing until
confirmed.

**Scheduling:** `~/.claude/scheduled-tasks/`. Logic lives in a plain CLI so launchd, cron,
or an MDM agent can drive it unchanged.

### 4.4 Retention is a hard constraint

Claude prunes `projects/` on a cleanup cycle (observed ~36 days, governed by
`cleanupPeriodDays` — **read it, don't assume it**). Transcripts past the window are gone
permanently. Therefore: daily cadence well inside the window, alert when a machine's gap
approaches retention, and run `--full` immediately at install.

### 4.5 Privacy

**`local` mode only.** Classification runs on-device; only labels, confidences, and
rationales leave the machine. No prompt text ever leaves a developer's laptop.

---

## 5. Classification

**Every session is classified by an LLM, retrospectively, once per session.** There is no
deterministic classification path — the collector is deterministic about *cost*, never
about *meaning*.

Retrospective classification sees the **entire session** rather than guessing from the
first prompt, and costs one call per session instead of one per prompt.

### 5.1 The Attribution Policy is a versioned prompt bundle

The service is the single source of truth for **how to classify**, not just the label
list. Every dimension carries its own prompt text.

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

Admins tune fleet-wide classification by editing text in one place — no client release.
Corrections are promoted into `examples` and ship to every client on next fetch.

### 5.2 Flow

1. Task calls **`get_attribution_policy()`** over MCP. Cached with a TTL; on fetch failure
   use the last good bundle and flag records as classified against a stale version.
2. Assemble **all** typed prompts in order, repo, branch, files touched,
   `ai-title`/`last-prompt` if present, and the policy bundle.
3. A cheap fast model returns structured JSON: three labels, a confidence each, a
   one-sentence rationale.
4. **`unclassifiable` is a first-class answer.** If nothing fits, the classifier says so
   rather than forcing a label. These cluster in the admin view as *work the taxonomy does
   not describe*.
5. `conf < 0.7` on any dimension → `needs_review`, into the owner's queue.
6. Publish via **`publish_attribution()`**; corrections written to
   `classification_corrections` and promoted into `policy_examples`.

### 5.3 Consequences of local classification

- **No reclassification.** Changing policy does not re-label history.
- **The review queue shows rationale and confidence, not the prompt.** Users can see their
  own prompts locally and correct the record, so disputes resolve — just not from the
  reviewer's side alone.
- **Fleet drift is unmeasured**, but stamped via `policy_version` + `classifier_version`.

### 5.4 Cost discipline

- **Self-metering** — `classifier_cost_usd` per classification, surfaced in-product.
- **Session grain** — one call per session.
- **Prompt-hash cache** — identical prompt sets on the same repo+branch reuse the result.

Every classification stores rationale, confidence, `policy_version`, and
`classifier_version`. An unexplainable chargeback gets disputed, then ignored.

---

## 6. Productivity gain — the one modelled metric

Cost attribution answers *where the money went*. It does not answer *was it worth it*.
Productivity gain does, and it is the only number in the product that is not measured —
so the design goal is that **a reader can never mistake it for one that is.**

### 6.1 The split

```
productivity  =  units of work produced  ×  what a unit costs a human
                 └──── MEASURED ────┘        └───── ASSUMED ──────┘
                 transcripts, git,            a baseline the customer
                 session timestamps           owns, with a named source
```

Both halves are stored, versioned, and reported separately. The API never returns a bare
speedup figure: every response carries `measured`, `modelled`, `sensitivity`,
`modelled_coverage`, `assumptions`, and `caveats`.

### 6.2 Why time, not volume

The obvious formulation — *"a human writes 50 lines a day, Claude wrote 500, therefore
10×"* — fails on contact with a finance team. Lines of code is a discredited proxy, and
500 lines that get reverted is negative productivity.

Instead:

- **Actual human time** is measured from `started_at`/`ended_at`. Real data, already in
  the ledger.
- **Human-equivalent time** is units × baseline minutes.
- **Speedup** is the ratio.

This sidesteps volume proxies entirely and makes the numerator something we observe rather
than something we assert.

### 6.3 Baselines are customer-owned records, not constants

A baseline carries `human_minutes_per_unit`, a low/high band, a free-text `source`, and a
`source_type` from an ordered set:

| `source_type` | Meaning |
|---|---|
| `customer_measured` | the customer measured it |
| `team_survey` | the team self-reported |
| `industry_estimate` | external benchmark |
| `vendor_claim` | vendor-supplied — treat with caution |
| `unset` | no source — **not credible**, and the report says so |

Baselines live in the policy bundle, so they version with it and reach every client on the
next fetch. Editing one republishes the policy.

### 6.4 Sensitivity is the feature

Every result reports the speedup at the low and high end of the customer's own band. A
CFO who disputes *"10 minutes a slide"* changes it to 6 and watches every number move.
That is the difference between a model and a claim — and when the band crosses 1.0, the
product says so in the headline rather than quoting the midpoint.

### 6.5 Two deliberate conservatisms

- **Only accepted work counts.** A session whose output was reverted contributes its full
  cost and zero productivity, so rework actually bites.
- **Human time is capped per session** (`org.session_minutes_cap`, default 90 min).
  Wall-clock overstates attention — people leave sessions open. The cap is configurable
  and stated in every response.

### 6.6 Extraction guidance biases toward undercounting

Counting units is a second job for the classifier, driven by its own prompt in the policy
bundle (`output_extraction_guidance`). It instructs: count only completed, kept work;
never count drafts, rejected work, or things merely discussed; and **when unsure between
two values, return the lower one** — an inflated count discredits every other number in
the report.

Units carry a `basis` of `measured` (from git or an outcome event, e.g. a merged PR) or
`model_extracted` (the classifier counted them). The two are reported separately, because
they are not equally trustworthy.

### 6.7 Stated limits

These are surfaced in-product as caveats, not buried here:

- Coverage below 50% means the speedup describes a subset, and says so.
- A majority of `model_extracted` units is disclosed as a percentage.
- Baselines with `unset` or `vendor_claim` sources trigger a warning and mark the policy
  not-ready.
- Hours saved is never converted to dollars.

---

## 7. Data model

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
         principal_type, parent_session_id,
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

productivity_baselines(id, org_id, task_type_key, unit, unit_plural,
                       human_minutes_per_unit, minutes_low, minutes_high,
                       source, source_type, set_by, set_at, notes, active)

work_outputs(id, session_pk, unit, count,
             basis,                      -- measured | model_extracted
             detail)

price_book(model, effective_from, input_per_mtok, output_per_mtok,
           cache_read_per_mtok, cache_write_per_mtok)
budgets(id, org_id, scope_type, scope_id, period, amount_usd)
```

**Store raw token counts alongside computed cost** so the ledger is recomputable when the
price book changes. Client-reconstructed cost drifts from the invoice; recomputability
keeps that drift auditable.

`principal_type` and `parent_session_id` are populated from `isSidechain` so subagent
spend is attributable.

---

## 8. API and MCP

```
POST   /v1/events                       batch ingest (org API key)
POST   /v1/sessions/{id}/outcomes       outcome artifacts
GET    /v1/ledger                       session-grain query
GET    /v1/rollup                       ?group_by=initiative|user|task_type
                                         |activity|cost_center&from&to&surface
GET    /v1/reports/executive            finance view
GET    /v1/reports/initiative/{key}     initiative owner view
GET    /v1/reports/me                   self view
GET    /v1/review-queue
POST   /v1/classifications/{id}/correct
GET|PUT /v1/policy                      Attribution Policy (admin)
GET|PUT /v1/baselines                   productivity baselines (admin)
GET    /v1/reports/productivity         measured + modelled, with assumptions
PUT    /v1/org/settings                 session_minutes_cap
GET|PUT /v1/budgets
POST   /internal/reconcile              Cloud Scheduler: poll GitHub for merges

/mcp                                    streamable HTTP, per-user OAuth
  get_attribution_policy()              versioned prompt bundle (§5.1)
  publish_attribution(work_unit)        idempotent on external_ref
  query_my_spend(period)                self view
  query_initiative(key, period)         role-scoped
```

**Trust boundary.** Everything arriving over MCP is model-generated content, not fact:

- **Identity comes from the OAuth token, never the payload.**
- **Cost is never accepted from the model** — the service computes it or marks it unknown.
- **Initiatives must pre-exist** in the policy; the skill cannot create one.
- Per-user rate limits; every published record is visible and correctable by the user it
  names.

A chat or Cowork session may be reading untrusted documents. A prompt-injected file saying
*"record this against the Platform initiative"* must not move money on someone else's
books.

**Auth:** org API key (hashed at rest) for ingest; Google OIDC for the dashboard.

---

## 9. Deployment (GCP)

| Concern | Service |
|---|---|
| API + MCP | **Cloud Run** — FastAPI, scale-to-zero, `/mcp` on the same service |
| Ledger | **Cloud SQL / Postgres 16**; SQLite for local dev |
| Outcome reconciliation | **Cloud Scheduler** → `/internal/reconcile`, hourly |
| Secrets | **Secret Manager** |
| Dashboard | published **Artifact**, read-only against the API |

---

## 10. Risks with build implications

| Risk | Mitigation |
|---|---|
| **Transcript retention** — a gap past the window loses data permanently | Daily runs self-heal within the window; alert as a machine's gap approaches retention; `--full` at install |
| **Backfill cost spike** | `--dry-run` preview required; `--since` to bound scope |
| **Classifier cost** | Self-metering surfaced in-product; session grain; prompt-hash cache |
| **Cost drift vs. invoice** | Recomputable ledger; never claim invoice truth |
| **Silent ownership reassignment** — re-uploading a machine's history as a different user moves the rows rather than duplicating them | Correct dedupe grain, but the response should report the reassignment and the collector should require an explicit flag for it |
| **Identity is derived on the MCP path and asserted on the REST path** — `/v1/events` trusts `user_email` in the payload | Acceptable while the org key is a deployment secret; not once it is published. Align the REST path with the MCP path before any real deployment |
| **Prompt injection via MCP** | Identity from OAuth token not payload; cost never model-supplied; initiatives must pre-exist; rate limits |
| **Skill-path coverage is biased**, not merely incomplete — it misses expensive sessions disproportionately | Never extrapolate skill coverage to a total; report paths separately |
| **Productivity gain is modelled, and a bad baseline discredits the whole product** | Measured and modelled never merged in a response; mandatory source + trust level; sensitivity band on every figure; ungrounded baselines block policy readiness; never converted to dollars |
| **Unit counts are inflated by the classifier** | Extraction prompt biases to undercount; `basis` separates measured from extracted; % extracted disclosed as a caveat |
| **Surveillance perception** | Self-view always on; users correct their own data; `local` mode; aggregate to initiative, never rank individuals |

---

## 11. Build order

*(Steps 1–5 are built. See README for status.)*

1. **Service skeleton** — FastAPI, schema, migrations, SQLite local / Postgres on GCP
2. **`/v1/events` ingest** + price book + cost computation
3. **Seed generator** — synthetic 90-day org: 40 users, 6 initiatives, 4 cost centers
4. **Rollup + report endpoints** with role scoping
5. **MCP server** — `/mcp`, four tools, per-user bearer tokens, idempotent publish ✅
6. **Classifier** — policy bundle, structured output, confidence, `unclassifiable`,
   work-unit extraction, self-metering
7. **Collector CLI** — transcript parse (Code + Cowork), usage rollup, git outcomes,
   watermark; `--full` with `--dry-run`, plus daily incremental; two scheduled tasks
8. **Attribution skill** — classify-and-publish for chat
9. **Dashboard** — three role-scoped views, published as an Artifact
10. **Deploy** — Cloud Run + Cloud SQL + Scheduler reconciler
11. **README** — run locally in one command, deploy in one command

Steps 1–4 are the service and are built first: it is the contract every other component is
a client of, and the expensive thing to change once there is data in the ledger. Seed data
lands at step 3 so the dashboard is demoable before the collector exists.
