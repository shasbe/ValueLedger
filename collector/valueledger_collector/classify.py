"""LLM classification, on the machine.

Every session is classified retrospectively, once, from the user's own prompts,
against the organization's Attribution Policy. Two properties matter:

  * **It runs locally.** Only labels, confidences and a rationale leave the
    machine — prompt text never does. That is what makes the collector
    deployable inside an org that would never ship prompts to a third party.
  * **It meters itself.** A cost-attribution tool that quietly costs money is
    self-defeating, so the classifier's own spend is reported per session and
    surfaced in the product as a percentage of tracked spend.

`unclassifiable` is a first-class answer. Forcing a wrong label is worse than
admitting the taxonomy has a gap, because the gap is itself the useful signal.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

# Bulk classification over every session: a small fast model is the right tool,
# and keeping this cheap is the difference between the product being credible
# and being an embarrassment.
DEFAULT_MODEL = "claude-haiku-4-5"
CLASSIFIER_VERSION = "clf-0.1.0"

# USD per million tokens for the classifier itself, for self-metering.
MODEL_RATES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

MAX_PROMPT_CHARS = 6000


class WorkUnit(BaseModel):
    unit: str = Field(description="Unit key from the policy baselines, e.g. 'slide'.")
    count: float = Field(description="How many were completed and kept.")
    detail: str | None = Field(default=None, description="One short phrase of evidence.")


class Classification(BaseModel):
    initiative_key: str | None = Field(
        default=None,
        description="Key from the policy, or null if nothing genuinely fits.")
    task_type_key: str | None = None
    activity_key: str | None = None
    conf_initiative: float = Field(default=0.0, ge=0.0, le=1.0)
    conf_task_type: float = Field(default=0.0, ge=0.0, le=1.0)
    conf_activity: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(
        description="One sentence. The only evidence a reviewer sees, because "
                    "prompt text never leaves the machine.")
    work_units: list[WorkUnit] = Field(default_factory=list)


@dataclass
class ClassifyResult:
    classification: Classification | None
    cost_usd: float = 0.0
    model: str = DEFAULT_MODEL
    error: str | None = None


def _dims(items: list[dict], label: str) -> str:
    if not items:
        return f"(no {label} defined)"
    return "\n".join(
        f"- {d['key']}: {d.get('name','')}\n    {(d.get('classification_guidance') or '').strip()}"
        for d in items)


def build_prompt(policy: dict, session) -> str:
    baselines = policy.get("baselines") or []
    bl = "\n".join(
        f"- task_type '{b['task_type']}' is counted in '{b['unit']}'"
        f" ({b.get('unit_plural') or b['unit']})"
        for b in baselines) or "(no baselines defined — return an empty work_units list)"

    prompts = []
    budget = MAX_PROMPT_CHARS
    # Keep the first and last prompts: the first states intent, the last shows
    # where the work landed. The middle is the most droppable part.
    ordered = session.prompts
    if len(ordered) > 12:
        ordered = ordered[:6] + ["[... middle of session omitted ...]"] + ordered[-6:]
    for p in ordered:
        chunk = p[:900]
        if budget - len(chunk) < 0:
            break
        prompts.append(chunk)
        budget -= len(chunk)

    ctx = [
        f"surface: {session.surface}",
        f"repo: {session.repo or '(none)'}",
        f"branch: {session.branch or '(none)'}",
    ]
    if session.title:
        ctx.append(f"session title: {session.title}")

    return f"""{policy.get('global_guidance', '')}

## Initiatives
{_dims(policy.get('initiatives', []), 'initiatives')}

## Task types
{_dims(policy.get('task_types', []), 'task types')}

## Activities
{_dims(policy.get('activities', []), 'activities')}

## Counting output
{policy.get('output_extraction_guidance', '')}

Units to count, by task type:
{bl}

## Session context
{chr(10).join(ctx)}

## The user's prompts, in order
{chr(10).join(f'{i+1}. {p}' for i, p in enumerate(prompts))}

Return the classification. Use only keys that appear above. If no initiative
genuinely fits, set initiative_key to null rather than choosing the closest one.
"""


def classify(policy: dict, session, model: str = DEFAULT_MODEL,
             api_key: str | None = None) -> ClassifyResult:
    try:
        import anthropic
    except ImportError:
        return ClassifyResult(None, error="anthropic SDK not installed")

    # Let the SDK resolve credentials itself. It tries ANTHROPIC_API_KEY, then
    # ANTHROPIC_AUTH_TOKEN, then the OAuth profile on disk from `ant auth login`
    # or Claude Code — so a developer who is already signed in needs no key.
    try:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    except Exception as e:  # noqa: BLE001
        return ClassifyResult(None, error=f"no usable credentials: {str(e)[:120]}")
    try:
        resp = client.messages.parse(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": build_prompt(policy, session)}],
            output_format=Classification,
        )
    except Exception as e:  # noqa: BLE001
        return ClassifyResult(None, model=model, error=str(e)[:200])

    usage = getattr(resp, "usage", None)
    cost = 0.0
    if usage:
        inp, out = MODEL_RATES.get(model, MODEL_RATES[DEFAULT_MODEL])
        cost = ((getattr(usage, "input_tokens", 0) or 0) * inp
                + (getattr(usage, "output_tokens", 0) or 0) * out) / 1_000_000

    return ClassifyResult(resp.parsed_output, cost_usd=cost, model=model)


def estimate_cost(policy: dict, sessions, model: str = DEFAULT_MODEL) -> float:
    """Rough pre-flight estimate for --dry-run.

    A backfill that classifies hundreds of sessions is a cost spike caused by the
    cost-control tool, so it is priced before it is spent.
    """
    inp, out = MODEL_RATES.get(model, MODEL_RATES[DEFAULT_MODEL])
    policy_chars = sum(
        len((d.get("classification_guidance") or "") + (d.get("name") or ""))
        for group in ("initiatives", "task_types", "activities")
        for d in policy.get(group, []))
    policy_chars += len(policy.get("global_guidance", "")) + \
        len(policy.get("output_extraction_guidance", ""))
    total = 0.0
    for s in sessions:
        in_tok = (policy_chars + min(s.prompt_chars, MAX_PROMPT_CHARS) + 400) / 3.5
        total += (in_tok * inp + 320 * out) / 1_000_000
    return total
