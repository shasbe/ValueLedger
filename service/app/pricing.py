"""Price book and cost computation.

Cost is computed server-side from raw token counts, never accepted from a client
(SPEC 7 trust boundary). Raw counts are stored alongside the computed cost so the
whole ledger is recomputable when prices change.

Rates are Anthropic first-party API list prices, USD per million tokens.
Cache tiers follow the standard multipliers against the input rate
(5m write = 1.25x, 1h write = 2x, read = 0.1x) except where a model prices cache
reads explicitly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import PriceBook, UsageEvent

PRICE_BOOK_VERSION = "2026-06-24"

# model: (input, output, cache_read, cache_write_5m, cache_write_1h)
LIST_PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-fable-5-1":  (10.00, 50.00, 0.25, 12.50, 20.00),   # cache read priced explicitly
    "claude-mythos-5-1": (10.00, 50.00, 0.25, 12.50, 20.00),
    "claude-fable-5":    (10.00, 50.00, 1.00, 12.50, 20.00),
    "claude-opus-5-5":   (4.00,  20.00, 0.20,  5.00,  8.00),   # cache read priced explicitly
    "claude-opus-5":     (5.00,  25.00, 0.50,  6.25, 10.00),
    "claude-opus-4-8":   (5.00,  25.00, 0.50,  6.25, 10.00),
    "claude-opus-4-7":   (5.00,  25.00, 0.50,  6.25, 10.00),
    "claude-opus-4-6":   (5.00,  25.00, 0.50,  6.25, 10.00),
    "claude-sonnet-5":   (2.00,  10.00, 0.20,  2.50,  4.00),
    "claude-sonnet-4-6": (3.00,  15.00, 0.30,  3.75,  6.00),
    "claude-haiku-4-5":  (1.00,   5.00, 0.10,  1.25,  2.00),
}

_MTOK = 1_000_000.0


def seed_price_book(db: Session) -> int:
    """Load list prices. Idempotent — skips models already present."""
    existing = {m for (m,) in db.query(PriceBook.model).all()}
    added = 0
    effective = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for model, (inp, out, cr, cw5, cw1) in LIST_PRICES.items():
        if model in existing:
            continue
        db.add(PriceBook(
            model=model, effective_from=effective,
            input_per_mtok=inp, output_per_mtok=out, cache_read_per_mtok=cr,
            cache_write_5m_per_mtok=cw5, cache_write_1h_per_mtok=cw1,
            version=PRICE_BOOK_VERSION,
        ))
        added += 1
    db.commit()
    return added


def normalize_model(model: str | None) -> str:
    """Strip date suffixes so `claude-opus-5-20260401` prices as `claude-opus-5`."""
    if not model:
        return "unknown"
    m = model.strip()
    if m in LIST_PRICES:
        return m
    # Longest known prefix wins, so `claude-opus-5-5` is not matched by `claude-opus-5`.
    for known in sorted(LIST_PRICES, key=len, reverse=True):
        if m.startswith(known):
            return known
    return m


def rates_for(db: Session, model: str) -> tuple[float, float, float, float, float] | None:
    key = normalize_model(model)
    row = (
        db.query(PriceBook)
        .filter(PriceBook.model == key)
        .order_by(PriceBook.effective_from.desc())
        .first()
    )
    if row:
        return (row.input_per_mtok, row.output_per_mtok, row.cache_read_per_mtok,
                row.cache_write_5m_per_mtok, row.cache_write_1h_per_mtok)
    return LIST_PRICES.get(key)


def price_usage_event(db: Session, ev: UsageEvent) -> None:
    """Set cost_usd on a usage event. Unknown models are stored with priced=False
    rather than silently costed at zero — an unpriced model must be visible."""
    rates = rates_for(db, ev.model)
    if rates is None:
        ev.cost_usd = 0.0
        ev.priced = False
        ev.price_book_version = PRICE_BOOK_VERSION
        return
    inp, out, cr, cw5, cw1 = rates
    ev.cost_usd = (
        (ev.input_tokens or 0) * inp
        + (ev.output_tokens or 0) * out
        + (ev.cache_read_tokens or 0) * cr
        + (ev.cache_write_5m_tokens or 0) * cw5
        + (ev.cache_write_1h_tokens or 0) * cw1
    ) / _MTOK
    ev.priced = True
    ev.price_book_version = PRICE_BOOK_VERSION


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
                  cache_read: int = 0, cache_write_5m: int = 0,
                  cache_write_1h: int = 0) -> float:
    """Price without a DB session — used by the collector's --dry-run preview."""
    rates = LIST_PRICES.get(normalize_model(model))
    if rates is None:
        return 0.0
    inp, out, cr, cw5, cw1 = rates
    return (input_tokens * inp + output_tokens * out + cache_read * cr
            + cache_write_5m * cw5 + cache_write_1h * cw1) / _MTOK
