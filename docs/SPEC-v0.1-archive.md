# ValueLedger — Product Spec v0.1

**Status:** Draft for build
**Author:** Sudhir Hasbe (PM)
**Date:** 2026-09-26
**Context:** Enterprise PM take-home — "Paying for Intelligence"

---

## 1. The problem, stated precisely

Claude Enterprise bills consumption across Claude Code, Cowork, and chat. Every dollar is
fungible and **unlabeled**. Nobody can answer three questions that every enterprise buyer
asks within 90 days of signing:

1. **Where did the money go?** Not "which seat" — *which business initiative.*
2. **What came out of it?** Not "tokens consumed" — *what shipped.*
3. **What will next quarter cost?** Not "trailing average" — *committed work vs. budget.*

The root cause is not reporting. It is that **the atomic unit of attribution does not
exist**. There is no record anywhere that says *"this $4.12 of inference was spent on the
Payments Migration initiative, doing refactor work, and produced commit a3f91c which
merged in PR #4412."* Every downstream capability — chargeback, forecasting, ROI,
budget policy, procurement defense — is blocked on that record not existing.

Agents make this strictly worse. Autonomous consumption has no human in the loop to
vouch for it, so the fraction of spend with *zero* attributable intent grows every quarter.

### The wedge

**Build the ledger first.** ValueLedger creates the missing atomic record: a
**cost-to-business-work attribution entry**, emitted at the point of work, classified
against the enterprise's own initiative taxonomy, and joined to verifiable output.

We start with **Claude Code** because it is the only surface that has all four properties
at once:

| Property | Claude Code | Cowork | Chat |
|---|---|---|---|
| Highest per-seat spend | ✅ | ~ | ❌ |
| Rich classifiable context (repo, branch, prompt) | ✅ | ~ | ~ |
| A client-side insertion point that exists today (hooks) | ✅ | ❌ | ❌ |
| **Verifiable ground-truth outcomes** (commits, PRs, tests) | ✅ | ❌ | ❌ |

That last row is the real reason. Claude Code is the one place where we can prove the
output side of the ratio instead of asserting it. We earn the right to talk about ROI
where ROI is *checkable*, then extend the schema to surfaces where it isn't.

---

## 2. Customers and views

Three personas, one ledger, role-scoped views. This is deliberate: a ledger that only
finance can see gets ignored; a ledger only devs can see never reaches a budget meeting.

### 2.1 Initiative Owner — *primary*
Owns a named business initiative and its budget. Needs to know if the spend on their
initiative is producing shipped work, and where it is leaking.

**Sees:** their initiatives only. Burn vs. budget, breakdown by task type / activity /
contributor, cost per merged PR, dark spend, rework spend, and the low-confidence
classification review queue.

**Job to be done:** "Defend or reallocate my initiative's Claude budget at the next
planning review."

### 2.2 CFO / Finance — *company-level*
Needs predictability and a defensible chargeback story.

**Sees:** everything, aggregated. Company total, by cost center and initiative, trend
and forecast vs. budget, top movers quarter-over-quarter, attribution coverage %, and
unattributed spend as an explicit line item.

**Job to be done:** "Approve next year's Claude Enterprise contract with a number I can
defend to the board."

### 2.3 Individual Contributor — *self-service*
Every user can see their own record, always. This is non-negotiable for adoption and for
privacy posture: no surveillance product that hides the data from the person it describes
survives contact with an engineering org.

**Sees:** their own sessions only. Personal spend by initiative and task type, their own
outputs, their most expensive sessions, and the ability to **correct their own
classifications** — which is also our highest-quality training signal.

**Job to be done:** "Understand and correct how my work is being counted."

### 2.4 Admin
Manages the Attribution Policy (initiatives, task types, activities, classification
guidance), org settings, privacy mode, API keys, budgets.

---

## 3. What we will and will not claim

**We will claim:** exact attributed cost, and verifiable output counts.

**We will not claim:** a fabricated dollar value of work produced.

A representative initiative card reads:

```
Payments Migration                     Q3 2026
  Attributed spend        $8,412   (94% of sessions classified)
  Merged PRs                  31
  Cost per merged PR        $271
  Commits                    218
  Dark spend               $1,514   (18% — sessions with no durable artifact)
  Rework spend               $602   (7% — work later reverted)
  Budget                  $12,000   →  70% consumed, 62% through period
```

No line on that card is an assumption. That is the entire product thesis: **CFOs do not
distrust the value of AI, they distrust invented numbers about it.** The honest ratio
beats the flattering one, and it is the only version that survives a second quarter.

### Three metrics only we can produce

These are the differentiators — nothing in the existing FinOps or observability stack
can compute them, because they require cost and outcome joined at the session grain.

- **Dark spend** — spend on sessions that produced no durable artifact. Immediately
  actionable, and the first number every initiative owner reacts to.
- **Rework spend** — spend on work later reverted or superseded.
- **Attribution coverage** — the honesty meter, reported as two numbers that must never
  be collapsed into one:
  - *Classification coverage* — % of observed work mapped to an initiative.
  - *Cost coverage* — % of that work whose dollar cost was **measured** rather than
    estimated or unknown.

  Claude Code scores high on both. Cowork and chat score high on the first and low on the
  second (§7). If cost coverage is 60%, we say 60%.

---

## 4. Architecture

```
  DETERMINISTIC PATH                      MODEL-MEDIATED PATH
┌──────────────────────┐            ┌──────────────────────────┐
│  Claude Code         │            │  Cowork / chat / any     │
│  ~/.claude/projects/ │            │  MCP-capable client      │
│  ┌────────────────┐  │            │  ┌────────────────────┐  │
│  │ daily COLLECTOR│  │            │  │ attribution skill  │  │
│  └───────┬────────┘  │            │  └─────────┬──────────┘  │
│  prompts + MEASURED  │            │  LABELS ONLY (model      │
│  token usage (reads  │            │  cannot see its cost)    │
│  transcripts on disk)│            │                          │
└──────────┼───────────┘            └──────────┼───────────────┘
           │ HTTPS batch, API key              │ MCP, per-user OAuth
           ▼                                   ▼
┌──────────────────────────────────────────────────────────────┐
│  AttributionService  (Cloud Run, FastAPI)                    │
│                                                               │
│  /v1/events  ──┐                      ┌── MCP server          │
│                ├─► normalize ─► cost ─┤   publish_attribution │
│  /mcp        ──┘   (price book)       │   get_policy          │
│                         │             └── query_spend (read)  │
│                         ▼                                     │
│                  Classifier (LLM)  ◄── service-side, batch path│
│                         │              (skill path classifies │
│                         ▼               in-client)            │
│  /v1/rollup  ◄──── Ledger (Postgres)                          │
│  /v1/reports       every row carries collection_method        │
│                    + cost_basis                               │
│                         ▲                                     │
│  Cloud Scheduler ──► Outcome Reconciler (GitHub: PR merged?)  │
└──────────────────┬───────────────────────────────────────────┘
                   │ read-only, OIDC
                   ▼
        ┌────────────────────────┐
        │  Dashboard (Artifact)  │
        │  role-scoped views     │
        └────────────────────────┘
```

### GCP deployment

| Concern | Service |
|---|---|
| API | **Cloud Run** (FastAPI container, scale-to-zero) |
| MCP server | same Cloud Run service, streamable-HTTP transport at `/mcp` |
| Ledger | **Cloud SQL / Postgres 16** (SQLite for local dev) |
| Async classification | **Pub/Sub** topic + push subscription to Cloud Run |
| Outcome reconciliation | **Cloud Scheduler** → `/internal/reconcile` (hourly) |
| Secrets | **Secret Manager** (API keys, Anthropic key, GitHub token) |
| Images | **Artifact Registry** |
| Identity | Google OIDC for dashboard; org API keys for ingest |

Scale-to-zero matters for the demo and is honest for the category: a cost-control
product that costs meaningful money to run is a bad look.

---

## 5. Data model

### 5.1 Attribution dimensions

| Dimension | Source | Notes |
|---|---|---|
| `user_email` | git config / env / OIDC | identity |
| `cost_center` | directory mapping | CFO rollup |
| `initiative` | **LLM classified** | enterprise-defined |
| `task_type` | **LLM classified** | coding, strategy doc, marketing artifact, data analysis, research, ops |
| `activity` | **LLM classified** | feature, bugfix, refactor, test, review, docs, exploration, scaffolding |
| `surface` | hook | `claude_code` \| `cowork` \| `chat` \| `api` |
| `repo` / `branch` | git | context + outcome join key |
| `principal_type` | hook | `human` \| `agent` \| `scheduled` |
| `parent_session_id` | hook | subagent / delegated spend |
| `delegation_chain` | derived | who authorized autonomous work |
| `collection_method` | ingest path | `collector` \| `mcp_skill` \| `usage_import` |
| `cost_basis` | derived | `measured` \| `estimated` \| `unknown` |

`principal_type` and `delegation_chain` exist in v1 even though v1 traffic is mostly
human. They are the schema hook for the agentic problem in §10 — we want the ledger to
already have a place to put autonomous spend before it dominates.

### 5.2 Schema

```sql
orgs(id, name, api_key_hash, privacy_mode, created_at)

users(id, org_id, email, display_name, cost_center, manager_email,
      role)                              -- member | initiative_owner | finance | admin

attribution_policies(id, org_id, version, published_at, published_by, checksum,
                     global_guidance)    -- org-wide classifier preamble

initiatives(id, org_id, policy_version, key, name, description,
            classification_guidance,     -- per-initiative prompt text
            owner_email, budget_amount, budget_period, cost_center, status)

task_types(id, org_id, policy_version, key, name, description, classification_guidance)
activities(id, org_id, policy_version, key, name, description, classification_guidance)

policy_examples(id, org_id, policy_version, dimension,   -- few-shot, from corrections
                example_text, label, source_correction_id)

sessions(id, org_id, session_id UNIQUE, surface, user_email,
         principal_type, parent_session_id,
         repo, branch, cwd_hash, started_at, ended_at, client_version,
         collection_method,               -- collector | mcp_skill | usage_import
         cost_basis,                      -- measured | estimated | unknown
         external_ref)                    -- client-supplied conversation/work-unit id

usage_events(id, session_id, ts, model, request_id,
             input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens,
             cost_usd, price_book_version)

prompts(id, session_id, seq, ts, text, text_hash, char_count, redacted)

classifications(id, session_id,
                initiative_id, task_type_id, activity_id,
                conf_initiative, conf_task_type, conf_activity,
                rationale, classifier_model, classifier_cost_usd,
                classifier_version, policy_version,
                status,                  -- auto | needs_review | confirmed | corrected
                created_at)

classification_corrections(id, classification_id, corrected_by,
                           prev_json, new_json, note, ts)

outcome_events(id, org_id, session_id, type, ref, ts, metadata_json)
             -- type: commit | pr_opened | pr_merged | pr_closed
             --       | test_added | revert | file_changed

price_book(model, effective_from, input_per_mtok, output_per_mtok,
           cache_read_per_mtok, cache_write_per_mtok)

budgets(id, org_id, scope_type, scope_id, period, amount_usd)
```

Raw token counts are stored alongside computed cost so the entire ledger can be
**recomputed** when the price book changes. Cost reconstructed client-side will drift
from the invoice; recomputability is how we keep that drift auditable rather than
mysterious. See §10.1.

---

## 6. Collection: the daily batch collector

**Design note — verified on a real machine, 2026-09-26.** Claude Code already persists
everything this product needs. The collector reads it; it does not need to intercept it.

### 6.1 What is already on disk

| Path | Contents | Verdict |
|---|---|---|
| `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` | full transcripts | **the ledger** |
| `~/.claude/roiclaude/spend.db` | usage priced at API list (2093 rows / 20 sessions / $409.78 observed) | reference implementation of the cost side |
| `~/.claude/history.jsonl` | 9 lines, UI recall buffer | not a ledger — ignore |
| `~/.claude/stats-cache.json` | stale, partial, `costUSD: 0` | not authoritative — ignore |

Per transcript, per record:

- **Typed user prompts** — `type=="user"` where `message.content` is a **string**. Tool
  results are arrays; that is the separator. (`promptSource` is mostly null and cannot be
  used for this.) → the classification signal.
- **Per-request usage** on `type=="assistant"` — `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, and `cache_creation` split by 5m/1h TTL, plus `model` and
  `timestamp`. → the cost signal, at request grain.
- **Context** — `cwd`, `gitBranch`, `sessionId`, `version`, `isSidechain`.
  `isSidechain` is our subagent attribution (§5.1 `parent_session_id`).
- **Session summaries** — `ai-title` / `last-prompt`. Present in roughly half of sessions
  observed, so useful as a classifier hint but **never a dependency**.

### 6.2 The collector — one implementation, two scheduled tasks

**Prototype scope.** The collector is a local CLI. An enterprise deployment would replace
it with a managed hook or MDM-pushed agent (§6.6); that is explicitly *not* in scope here.

Both tasks are the **same code path with a different watermark bound** — same parse, same
dedupe, same POST. Build once, schedule twice.

| Task | Bound | Cadence | Purpose |
|---|---|---|---|
| **`collect --full`** | epoch (or `--since DATE`) | once at install, on demand | bootstrap + disaster recovery |
| **`collect`** | last successful watermark | daily | steady state |

Per run:

1. Enumerate transcripts modified since the bound.
2. Extract typed prompts, roll up usage per session per model, capture git context.
3. Capture outcomes: commits in the session window, files changed, lines added/removed,
   whether test files were touched.
4. POST the batch to `/v1/events`. Service prices it and enqueues classification.
5. Advance the watermark on success only.

Dedupe is on `session_id` + record uuid, so **every run is idempotent** and re-running
`--full` can never double-count.

**The daily run is self-healing.** Because it scans *"modified since watermark"* rather
than *"modified yesterday,"* a laptop asleep for a week catches up on its next tick with
no intervention. A missed run is therefore only unrecoverable if the gap exceeds the
retention window (§6.4) — which is the one thing worth alerting on.

**`--dry-run` is mandatory on the full run.** A backfill on a real machine could classify
hundreds of sessions in a single burst — a cost spike caused by the cost-control tool.
So a full run first reports *"will classify 247 sessions, estimated $2.10"* and spends
nothing until confirmed. A tool that meters its own backfill before running it
demonstrates the product thesis more convincingly than any dashboard tile.

**Scheduling, for the prototype:** Claude Code's own `~/.claude/scheduled-tasks/`. Zero
OS-level setup, cross-platform, and there is a pleasing symmetry in Claude scheduling the
job that measures Claude. The logic lives in a plain CLI underneath, so launchd, cron, or
an MDM agent can drive it unchanged.

### 6.3 Why batch beats a hook for classification

This is a design *upgrade*, not a compromise:

- **Complete information.** A `UserPromptSubmit` classifier sees the first prompt and must
  guess where the session is going. The batch job sees the **entire session** and
  classifies retrospectively. Strictly more accurate.
- **Cheaper.** One classification per session, not one per prompt.
- **Zero interactive risk.** The classifier is off the critical path entirely — no latency
  budget, no fail-open design, no spool-and-flush machinery.
- **It backfills.** ⭐ A new customer sees ~30 days of populated dashboard on install day
  rather than waiting a month to accumulate it. This materially de-risks the week-6 PMF
  signal in §11 — the initiative owner can make a decision in week 1.

**Cost:** attribution is up to 24h stale. Acceptable, and consistent with §10.4 — v1 is a
detective control, not a preventive one.

### 6.4 The retention constraint — a hard requirement

Claude Code prunes `projects/` on a cleanup cycle (observed window: ~36 days, governed by
`cleanupPeriodDays`). **Transcripts older than the retention window are gone
permanently.** Therefore:

- The collector must run on a cadence **well inside** the retention window. Daily, with
  alerting if a machine misses N consecutive runs.
- A missed window is unrecoverable data loss, not a delay. This is the collector's
  single most important operational property.
- Backfill on first install must run **immediately**, before the next cleanup.

### 6.5 Privacy modes

Prompts are intellectual property. This is the biggest deployment objection, and it gets
designed for rather than handled.

| Mode | Behavior | Scope |
|---|---|---|
| `local` | classification runs on-device; **only labels leave the machine** | **prototype — the only mode built** |
| `redacted` | secrets, tokens, file contents, PII scrubbed locally before send | v2, behind the enterprise hook |
| `full` | prompt text sent to the service | v2, behind the enterprise hook |

The prototype ships `local` only. No prompt text ever leaves a developer's machine, which
removes the single largest deployment objection at the cost of central classifier
improvement and reclassification (§8.3). The other two modes become reasonable only once
an enterprise hook puts the service inside the customer's own trust boundary (§6.6).

### 6.6 What the production version does differently

The local collector is a **prototype instrument**. A real deployment replaces it with an
enterprise hook or managed agent, for four reasons the prototype cannot address:

| | Prototype collector | Production hook / agent |
|---|---|---|
| Coverage | only as complete as the least-compliant laptop | fleet-guaranteed, centrally deployed |
| Timeliness | up to 24h stale | real-time, enabling **preventive** budget control (§10.4) |
| Durability | bounded by local transcript retention (§6.4) | server-side, no retention cliff |
| **Trust boundary** | third-party service — so classification **must** stay on-device (§8.3) | inside the customer's control plane — server-side classification becomes an intra-enterprise transfer |

This is a deliberate and stated trade, not an oversight. The prototype proves the
**ledger, the taxonomy, the classification, and the outcome joins** — which is the whole
product thesis — using data that already exists on disk, with no platform changes and no
fleet rollout. The collection mechanism is the most replaceable part of the design, and
swapping it changes nothing above `/v1/events`.

---

## 7. Multi-surface capture: MCP server + attribution skill

Local transcripts only exist for Claude Code. To reach Cowork and chat, the service also exposes an
**MCP server**, and we ship a **skill** that asks Claude to classify the work it just did
and publish the result through it.

These are two different kinds of instrument and the spec treats them differently:

| | **Collector** (Claude Code) | **Skill + MCP** (Cowork, chat) |
|---|---|---|
| Fires | deterministically, nightly | when the model remembers |
| Knows token usage | **yes** — reads the transcript | **no** |
| Produces | labels **and** measured cost | labels only |
| `cost_basis` | `measured` | `estimated` or `unknown` |
| Trust model | telemetry | self-report |

**The collector is the meter. The skill is a witness.** Both are useful; conflating them in a
single "spend" number would be exactly the kind of invented figure §3 rules out.

### 7.1 The constraint that shapes the design

In Cowork and chat, **the model cannot see its own token usage.** It has the
conversation, so it can classify *intent* well — which is the hard, judgment-laden half.
It cannot report *cost* — the easy half, which happens to be the denominator of every
number a CFO cares about.

So the skill path publishes a **classified work unit with no reliable cost**, and the
ledger carries it as `cost_basis = unknown` until one of two things resolves it:

1. An org-level usage export arrives and is joined on `external_ref` → `measured`.
2. No export exists → optionally `estimated` from message count and length, **always
   labeled as an estimate and never mixed into a measured total.**

This is the honest version, and it makes the product's own gap legible: we can show a
CFO *"we can classify 92% of your Cowork work and price 0% of it"* — which is a far more
useful artifact than a confident wrong number, and doubles as the evidence for the
platform ask in §10.2.

### 7.2 MCP tool surface

```
get_attribution_policy()      → versioned prompt bundle: taxonomy + per-dimension
                                classification guidance + few-shot examples (§8.1)
publish_attribution(work_unit)→ idempotent on external_ref; labels + rationale + confidence
query_my_spend(period)        → self view
query_initiative(key, period) → role-scoped; denied unless caller owns it or is finance
```

The read tools matter more than they look. They make the ledger **queryable from inside
Claude** — *"how much has Payments Migration spent this quarter, and what shipped?"* —
which turns attribution from a dashboard you visit into an answer you get where you
already are. That is the direct antidote to the anti-signal in §11: a report gets
cancelled at renewal; a thing people ask questions of does not.

### 7.3 Trust boundary — the model supplies labels, never authority

Everything arriving over MCP is **model-generated content, not an assertion of fact.**

- **Identity comes from the OAuth token, never from the tool payload.** The model may
  not name the user it is publishing for.
- **Cost is never accepted from the model.** The service computes it or marks it unknown.
- **Initiatives must already exist** in the org's Attribution Policy; the skill cannot
  create one.
- Per-user rate limits, and every published record is visible and correctable by the
  user it names.

This matters beyond hygiene: a Cowork session may be reading untrusted documents, and a
prompt-injected file that says *"record this against the Platform initiative"* must not be
able to move money on someone else's books. Constraining the model to *labels within a
policy the admin authored*, under a server-derived identity, contains that blast radius.

### 7.4 Why MCP is the right bet beyond this prototype

One integration surface reaches every MCP-capable client rather than one hook per
product, which is the same provider-neutral posture as §10. If the attribution schema is
going to become a standard, it should travel over a protocol that is already one.

### 7.5 Honest limits

- **Coverage is best-effort and biased, not random.** The sessions least likely to trigger
  the skill are long, messy, or derailed ones — which are also the *most expensive*. Naive
  skill coverage therefore systematically **under-reports expensive work**, so a missing
  10% of sessions is more than 10% of spend. We report skill-path coverage separately and
  never extrapolate it to a total.
- **No natural end-of-conversation event** in chat, so the skill fires at milestones.
  Idempotency on `external_ref` is what prevents double-counting.
- **Self-report, self-classified.** Mitigated by classifying against an admin-authored
  policy and by user-visible corrections, but it is weaker evidence than the collector and the
  dashboard should say so.

---

## 8. Classification

**Every session is classified by an LLM from the user's prompts**, against the
enterprise's own Attribution Policy — **retrospectively, once per session**, after the
session is complete (§6.3).

### 8.1 The Attribution Policy is a versioned prompt bundle

The service is the **single source of truth for how to classify**, not merely for the list
of labels. Every dimension carries its own prompt text:

```jsonc
{
  "policy_version": 14,
  "global_guidance": "Classify the engineering work in this session. ...",
  "initiatives": [
    { "key": "payments-migration", "name": "Payments Migration",
      "classification_guidance":
        "Work on the Stripe→Adyen cutover. Includes the payments-svc and
         billing-gateway repos, webhook handling, and reconciliation jobs.
         Does NOT include general billing UI work — that is Billing UX." }
  ],
  "task_types": [ /* ... same shape ... */ ],
  "activities": [ /* ... same shape ... */ ],
  "examples": [
    { "dimension": "initiative", "example_text": "fix idempotency key on refund retry",
      "label": "payments-migration" }
  ]
}
```

Two consequences worth naming:

- **The admin tunes fleet-wide classification by editing text in one place.** No client
  release, no redeploy. Classification quality becomes a content problem, which is the
  kind an initiative owner can actually fix themselves.
- **The correction flywheel closes.** Corrections (§8.2 step 6) are promoted into `examples` and
  ship to every client on the next fetch. The system improves from use, per org.

Every classification stamps `policy_version`, so a label can always be traced to the exact
prompt bundle that produced it.

### 8.2 Flow

1. Scheduled task calls **`get_attribution_policy()`** over MCP, receiving the current
   bundle. Cached locally with a TTL; on a fetch failure the task uses the last good
   bundle and flags the records as classified against a stale version.
2. For each completed session, the classifier assembles: **all** typed prompts in order,
   repo, branch, files touched, `ai-title`/`last-prompt` if present, and the policy bundle.
3. A cheap fast model returns structured JSON: the three labels, a confidence per label,
   and a one-sentence rationale.
4. **`unclassifiable` is a first-class answer.** If nothing in the policy fits, the
   classifier says so rather than forcing a label. These cluster in the admin view as
   *"work your taxonomy does not describe"* — which is how initiatives get discovered
   rather than assumed.
5. `conf < 0.7` on any dimension → `status = needs_review`, into the initiative owner's
   review queue.
6. Results published via **`publish_attribution()`**; corrections written to
   `classification_corrections` and promoted into `policy_examples`.

### 8.3 Where classification runs — and why the prototype runs it locally

Policy is **always** fetched from the service. The model call happens **on the machine**,
in the scheduled task. Only labels, confidences, and rationales leave.

Server-side classification would be better engineering — central logs, one classifier
version, free reclassification, no fleet drift. It is not available to us here, because
it requires every developer's prompts to leave their laptop for a third-party service.
That is the wrong trade for a prototype and an unnecessary one, since the classification
signal can be reduced to labels before it ever crosses the boundary.

**This is the second thing the enterprise hook buys** (§6.6). A managed hook runs inside
the customer's own control plane, so prompt data reaching the attribution service is an
*intra-enterprise* transfer, governed by the same policy that already governs Claude
Enterprise. That is categorically different from an individual laptop posting prompts
outward — and it is why server-side processing belongs at the hook level rather than
being retrofitted here.

**Accepted consequences for the prototype:**

- **No reclassification.** Changing the policy does not re-label history. Out of scope
  (§13); `/v1/reclassify` is a v2 endpoint.
- **The review queue shows the rationale, not the prompt.** An initiative owner asking
  *"why is this session mine?"* gets the model's reasoning and the confidence, not the
  evidence. The user can always see their own prompts locally and correct the record
  (§2.3), so disputes are still resolvable — just not from the reviewer's side alone.
- **Fleet drift is unmeasured.** Acceptable at prototype scale; stamped via
  `policy_version` + `classifier_version` so it becomes measurable later.

### 8.4 Cost discipline — the "who watches the watcher" problem

A cost-attribution product that adds meaningful recurring inference cost is
self-defeating. Three controls, all visible in the product:

- **Self-metering.** `classifier_cost_usd` is stored per classification and surfaced in
  the dashboard as its own line: *"Classification overhead: $31 (0.37% of tracked
  spend)."* We turn the obvious objection into a proof point.
- **Prompt-hash cache.** Identical prompt sets on the same repo+branch reuse the prior
  result.
- **Optional deterministic pins.** Admins may pin `repo → initiative` where the mapping
  is unambiguous; the LLM then only resolves task type and activity. Off by default,
  available when volume makes it worth it.
- **Batch grain.** One call per session rather than per prompt — a direct consequence of
  classifying retrospectively (§6.3), and the largest single cost reduction available.

### 8.5 Every classification is explainable

Rationale, confidence, policy version, and classifier version are stored on every row.
An initiative owner can always ask "why is this session mine?" and get an answer. An
unexplainable chargeback is a chargeback that gets disputed and then ignored.

---

## 9. API surface

```
POST   /v1/events                       batch telemetry ingest (API key)
POST   /v1/sessions/{id}/outcomes       outcome artifacts
GET    /v1/ledger                       filtered session-grain query
GET    /v1/rollup                       ?group_by=initiative|user|task_type|activity|cost_center
                                        &from&to&surface
GET    /v1/reports/executive            CFO view: totals, trend, forecast vs budget
GET    /v1/reports/initiative/{key}     initiative owner view
GET    /v1/reports/me                   self view

GET    /v1/review-queue                 low-confidence classifications
POST   /v1/classifications/{id}/correct apply a correction

GET    /v1/policy                       initiatives, task types, activities
PUT    /v1/policy                       update Attribution Policy (admin)
POST   /v1/reclassify                   re-run classification after a policy change  [v2]

GET    /v1/budgets  /  PUT /v1/budgets  budget config
POST   /internal/reconcile              Cloud Scheduler: poll GitHub for merge status

/mcp                                    MCP server, streamable HTTP, per-user OAuth
                                        tools: get_attribution_policy,
                                               publish_attribution,
                                               query_my_spend, query_initiative
```

**Auth:** org API key (hashed at rest) for ingest; Google OIDC for the dashboard.
Role scoping enforced server-side in the query layer, not the UI — `member` can only
ever read rows where `user_email` is their own.

---

## 10. What this product needs that isn't possible yet

This is the most important section. Four hard blockers, and what we do about each.

### 10.1 No customer-defined attribution tags in the billing path
There is no way to tag a request with `initiative=payments-migration` and have that tag
appear on the invoice or in the Admin usage export. We reconstruct cost client-side from
token counts × a price book, which will **drift from the real invoice** — cache tiering,
contract discounts, and batch pricing are all invisible to us.

> **Ask:** an `x-attribution-tags` request header, echoed back on Admin API usage
> records. This is the single highest-leverage platform change for this category.
>
> **Meanwhile:** store raw tokens so the ledger is fully recomputable; display
> *reconstructed* cost with an explicit variance-vs-invoice reconciliation view rather
> than pretending we have invoice truth.

### 10.2 Cowork and chat have no client-side hook surface, and no cost visibility
We partially route around this with the MCP server and attribution skill (§7) — enough to
prove that **intent classification generalizes across surfaces**. It does not close the
gap, and the prototype is built to demonstrate precisely why:

- Capture is model-mediated, so coverage is best-effort and **biased toward missing the
  expensive sessions**.
- The model cannot see its own token usage, so the skill path yields **labels with no
  measurable cost**. We can classify Cowork work and we cannot price it.

> **Ask:** (a) a client event surface for non-Code products, and (b) an org usage export
> carrying a session/conversation identifier we can join on. (b) is far cheaper and
> unlocks most of the value — we already have the labels; we need a key to attach dollars
> to.
>
> **Meanwhile:** the schema is surface-agnostic and carries `collection_method` and
> `cost_basis` on every row, so when an export appears, existing records are upgraded
> from `unknown` to `measured` **in place** — no migration, no re-collection.

### 10.3 Autonomous spend has no principal
When an agent spawns subagents, spends overnight on a schedule, or acts on a queue,
there is no human to attribute to and no provenance chain. This is the problem that
**gets worse every quarter**, and the reason to build the ledger now rather than later.

> **Ask:** delegation provenance in the session record — who authorized this run, under
> what policy, on whose behalf.
>
> **Meanwhile:** `principal_type` and `parent_session_id` in the schema, inferred from
> `SubagentStop` and invocation context. Imperfect, but it means agentic spend has a
> shape in the ledger rather than being a growing unlabeled blob.

### 10.4 No pre-flight cost estimation
You cannot know what a task will cost before running it. So budgets can only be
*observed*, never *enforced*. Real budget control — "this initiative is at 95%, require
approval" — is impossible today.

> **Ask:** a cost-estimation endpoint, or budget-aware stop conditions in the client.
>
> **Meanwhile:** v1 ships alerting and forecast-based burndown, and is explicit that it
> is a detective control, not a preventive one. We do not pretend otherwise.

**The strategy across all four:** build entirely client-side and provider-neutral now,
publish the attribution schema as an open spec, and use design-partner evidence as the
forcing function for native platform support. When native tags land, we swap out the
reconstruction layer and every other part of the product is unchanged — the ledger, the
taxonomy, the outcome joins, and the review flywheel are the durable assets.

---

## 11. Validation and path to PMF

### Design partners
5–8 enterprises with >$250k annual Claude Enterprise spend, with an in-house platform
team that can push `settings.json` fleet-wide.

### Signals, in order

| When | Signal | Threshold | What a miss means |
|---|---|---|---|
| Week 1–2 | **Install rate** | hook live on >50 devs | If they won't deploy it, the pain isn't real. Kill or re-wedge. |
| Week 3–4 | **Classification acceptance** | >85% uncorrected | Taxonomy or classifier is wrong — fixable. |
| Week 6 | **Decision change** ⭐ | ≥1 initiative owner reallocates, kills, or expands based on the dashboard | The real PMF signal. |
| Quarter | **Budget artifact** | ValueLedger data appears in a budget or renewal doc | Commercial validation. |

**Anti-signal to watch for:** dashboard opened once at setup and never again. That means
we built a report, not a workflow — and reports get cancelled at renewal.

**PMF definition:** *ValueLedger data is cited in the customer's quarterly budget
defense.* Not logins, not sessions ingested. Cited in the document where the money is
decided.

### Six months out
1. **Ledger** (now) — attribution + verifiable outcomes, Claude Code.
2. **Budgets & alerts** (M2) — burndown, forecast, threshold alerts to Slack.
3. **Multi-surface** (M3–4) — Cowork and chat as platform surfaces open up.
4. **Agentic attribution** (M4–5) — delegation chains as autonomous spend scales.
5. **Benchmarks** (M6) — anonymized cross-customer medians: *"your cost per merged PR is
   2.1× the p50."* This is the compounding moat — it requires N customers and cannot be
   bootstrapped by a competitor or built in-house.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| **Surveillance perception** kills adoption | Self-view is always on; users correct their own data; `local` privacy mode; we aggregate to initiative, never rank individuals |
| **Classifier cost/drift** (accepted trade-off) | Self-metering surfaced in-product, prompt-hash cache, optional deterministic pins, versioned policy + reclassify |
| **Cost drift vs. invoice** | Recomputable ledger, explicit reconciliation view, never claim invoice truth |
| **Taxonomy rot** — initiatives change faster than the policy | Policy versioning, `/v1/reclassify`, review queue surfaces "doesn't fit anything" clusters |
| **Anthropic ships this natively** | Likely, and fine — our durable assets are the taxonomy, outcome joins, correction flywheel, and cross-customer benchmarks. We should be provider-neutral by design. |
| **Transcript retention** — a gap exceeding the window loses data permanently | Daily runs are self-healing within the window; alert only when a machine's gap approaches retention; immediate `--full` on install |
| **Backfill cost spike** — a full run classifying hundreds of sessions at once | `--dry-run` cost preview required before any full run; `--since` to bound scope |
| **Dark spend metric is gameable** | Report it as a trend with a defined artifact threshold; treat gaming as a signal to talk to that team |
| **Skill-path coverage is biased**, not merely incomplete — it misses expensive sessions disproportionately | Never extrapolate skill coverage to a total; report collector and skill paths separately; reconcile against an org usage export where one exists |
| **Fleet classification drift** — client-side classifiers differ by machine, model, version | Stamp `policy_version` + `classifier_version` on every row; sample-audit agreement; server-side mode available |
| **Reclassification is bounded in `local` mode** — needs transcripts that may be pruned | Stated trade-off; orgs needing free reclassification run `redacted` mode |
| **Prompt injection via MCP** — untrusted content steering attribution | Identity from OAuth token not payload; cost never model-supplied; initiatives must pre-exist in policy; per-user rate limits; user-visible corrections |

---

## 13. Build order

1. Service skeleton — FastAPI, schema, migrations, SQLite local / Postgres on GCP
2. `/v1/events` ingest + price book + cost computation
3. Seed generator — synthetic 90-day enterprise: 40 users, 6 initiatives, 4 cost centers
4. Classifier — LLM, structured output, confidence, rationale, self-metering
5. Rollup + report endpoints with role scoping
6. **Collector CLI** — transcript parse, usage rollup, git outcomes, watermark; `--full`
   (with `--dry-run` cost preview) and daily incremental; wired as two scheduled tasks
7. **MCP server** — `/mcp`, the four tools, OAuth identity, idempotent publish
8. **Attribution skill** — classify-and-publish for Cowork/chat
9. Dashboard — three role-scoped views, published as an Artifact
10. Cloud Run deploy + Cloud SQL + Scheduler reconciler
11. README — run locally in one command, deploy in one command

**Explicitly out of scope for the prototype:** enterprise hook, server-side
classification, reclassification of history, `redacted`/`full` privacy modes, real-time
budget enforcement. Each is deferred for a stated reason (§6.6, §8.3, §10.4), not for
lack of time.

Seed data lands at step 3 deliberately: the dashboard must be demoable with realistic
volume before the hook exists, so the two tracks can proceed independently.
