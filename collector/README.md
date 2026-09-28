# ValueLedger Collector

Reads finished Claude Code and Cowork transcripts from `~/.claude/projects/`,
classifies each session against your organization's Attribution Policy, and
publishes cost + outcomes to the AttributionService.

It does not intercept a running session. Claude already writes everything needed
to disk; the collector reads it afterwards.

## Why batch, not a hook

A hook-time classifier sees the *first* prompt and has to guess where the session
is going. The collector sees the **whole session** and classifies retrospectively
— strictly more accurate, and one LLM call per session instead of one per prompt.
It also backfills, so a new install shows ~30 days of history immediately rather
than accumulating it.

The cost is that attribution is up to 24h stale. That is fine: this is a
detective control, not a preventive one.

## Privacy

Classification runs **on this machine**. Only labels, confidences and a
one-sentence rationale leave it. Prompt text never does.

## Install

```bash
pip install -r collector/requirements.txt
export VALUELEDGER_URL=https://your-service
export VALUELEDGER_API_KEY=vl_...
export VALUELEDGER_USER=you@example.com      # must exist in the org
```

**Credentials for the classifier** are resolved by the Anthropic SDK in this
order: `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then the OAuth profile
on disk from `ant auth login` or Claude Code. A developer already signed in to
Claude Code needs no key at all — which matters for fleet rollout, because
asking 400 engineers to each provision an API key is a deployment blocker.

## Use

```bash
# See what would happen, and what it would cost. Spends nothing.
python -m valueledger_collector.cli --full --dry-run

# Backfill everything
python -m valueledger_collector.cli --full

# Daily incremental (bounded by the watermark)
python -m valueledger_collector.cli
```

Useful flags: `--since 2026-08-01`, `--limit 10`, `--model claude-haiku-4-5`,
`--no-git`, `--root /custom/path`.

`--dry-run` is required practice before a first `--full`: a backfill can classify
hundreds of sessions at once, and a cost spike caused by the cost-control tool is
a bad look.

## Two scheduled tasks

One implementation, two bounds. The daily run is **self-healing** — it scans
"modified since watermark", not "modified yesterday", so a laptop asleep for a
week catches up on its next tick.

```bash
# once, at install
python -m valueledger_collector.cli --full

# daily, via launchd (macOS)
cat > ~/Library/LaunchAgents/com.valueledger.collect.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.valueledger.collect</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/env</string><string>python3</string>
    <string>-m</string><string>valueledger_collector.cli</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PYTHONPATH</key><string>/path/to/ValueLedger/collector</string>
    <key>ANTHROPIC_API_KEY</key><string>sk-ant-...</string>
    <key>VALUELEDGER_URL</key><string>https://your-service</string>
    <key>VALUELEDGER_API_KEY</key><string>vl_...</string>
    <key>VALUELEDGER_USER</key><string>you@example.com</string>
  </dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer></dict>
  <key>StandardOutPath</key><string>/tmp/valueledger-collect.log</string>
  <key>StandardErrorPath</key><string>/tmp/valueledger-collect.err</string>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.valueledger.collect.plist
```

### Retention is a hard constraint

Claude prunes `~/.claude/projects/` on a cleanup cycle (~30-36 days, governed by
`cleanupPeriodDays`). **Transcripts past the window are gone permanently**, so
the cadence must stay well inside it. A missed day is recoverable; a missed month
is not.

## What it reads

| Signal | Source |
|---|---|
| typed prompts | `user` records whose `message.content` is a **string** (tool results are lists) |
| token usage | `assistant` records, with cache creation split by 5m/1h TTL |
| repo / branch | `cwd`, `gitBranch` |
| subagent spend | `isSidechain` → `principal_type=agent` + `parent_session_id` |
| session hint | `ai-title` / `last-prompt`, when present |
| outcomes | `git log` in the session window: commits, files changed, tests touched, reverts |

Cowork is detected by its `scratch-workspaces` path and reported as its own
surface. Both surfaces carry `cost_basis=measured`, because transcripts contain
real token counts. Chat has no local transcript and is reached over MCP instead.

## State

`~/.valueledger/state.json` holds the watermark. It advances **only** on a
successful publish, so a failed flush is retried rather than skipped. Publishing
is idempotent on `session_id` + record uuid, so re-running `--full` can never
double-count.
