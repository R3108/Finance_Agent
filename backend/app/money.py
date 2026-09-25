"""Currency registry and formatting — the one place that turns minor units into text.

The whole app stores money as integer **minor units** (`amount_cents`) so every aggregate is
exact. That invariant only holds if every supported currency has exactly 2 decimal places, so
this registry deliberately lists only 2-decimal currencies. Adding a 0-decimal currency (JPY,
KRW) or a 3-decimal one (KWD, BHD) would silently change what `amount_cents` means in every
table and every analytic, so it must not be done by appending a row here.

Formatting is read from a `contextvars` context rather than passed through ~40 analytics
signatures. `set_currency()` is called once per request (the `current_user` dependency) and
once per agent run; everything downstream — insight copy, budget rationales, the offline
router, digest emails — formats in the signed-in user's currency without threading a
parameter through the call graph. A contextvar is task-local, so concurrent requests for a
rupee user and a dollar user never see each other's setting.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

DEFAULT_CODE = "INR"


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    name: str
    locale: str            # BCP-47 tag handed to Intl.NumberFormat in the browser
    grouping: str = "western"   # western = 1,234,567 | indian = 12,34,567
    region: str = "US"          # picks the demo dataset and merchant rule pack
    decimals: int = 2           # always 2: see the module docstring
    #: Multiplier for "round a suggestion to a tidy number" steps, relative to the US dollar.
    #: A budget rounded to the nearest $10 should round to the nearest ₹500, not ₹10 — without
    #: this, every rupee suggestion would carry meaningless precision.
    step: int = 1


CURRENCIES: dict[str, Currency] = {
    "INR": Currency("INR", "₹", "Indian Rupee", "en-IN", "indian", "IN", step=50),
    "USD": Currency("USD", "$", "US Dollar", "en-US", "western", "US"),
    "EUR": Currency("EUR", "€", "Euro", "de-DE", "western", "EU"),
    "GBP": Currency("GBP", "£", "British Pound", "en-GB", "western", "GB"),
    "AED": Currency("AED", "AED ", "UAE Dirham", "en-AE", "western", "AE", step=4),
    "SGD": Currency("SGD", "S$", "Singapore Dollar", "en-SG", "western", "SG"),
    "AUD": Currency("AUD", "A$", "Australian Dollar", "en-AU", "western", "AU"),
    "CAD": Currency("CAD", "C$", "Canadian Dollar", "en-CA", "western", "CA"),
}

#: Every symbol a formatted amount can start with. The agent's grounding guard uses this to
#: strip currency markers before comparing a figure against tool output, so a rupee answer is
#: verified exactly like a dollar one.
SYMBOLS = tuple(dict.fromkeys(c.symbol.strip() for c in CURRENCIES.values()))

_current: ContextVar[str] = ContextVar("currency_code", default=DEFAULT_CODE)


def normalize(code: str | None) -> str:
    """A supported currency code, falling back to the default rather than raising."""
    return code.upper() if code and code.upper() in CURRENCIES else DEFAULT_CODE


def get(code: str | None = None) -> Currency:
    return CURRENCIES[normalize(code) if code else current_code()]


def current_code() -> str:
    return _current.get()


def set_currency(code: str | None) -> None:
    """Set the currency for the current task (request or agent run)."""
    _current.set(normalize(code))


@contextmanager
def use_currency(code: str | None) -> Iterator[Currency]:
    """Format in `code` for the duration of the block, then restore the previous setting."""
    token = _current.set(normalize(code))
    try:
        yield CURRENCIES[_current.get()]
    finally:
        _current.reset(token)


def _group(whole: str, style: str) -> str:
    """Thousands separators. Indian grouping is 3 digits, then 2s: 1,23,45,678."""
    if style != "indian" or len(whole) <= 3:
        return f"{int(whole):,}"
    head, tail = whole[:-3], whole[-3:]
    parts: list[str] = []
    while len(head) > 2:
        head, chunk = head[:-2], head[-2:]
        parts.insert(0, chunk)
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def format_money(amount: float, code: str | None = None, decimals: int | None = None) -> str:
    """A major-unit amount as display text: `format_money(-1234.5)` -> '-₹1,234.50'.

    Negatives lead with the sign, matching the app's convention that money leaving an account
    reads as a negative figure rather than parentheses.
    """
    cur = get(code)
    places = cur.decimals if decimals is None else decimals
    sign = "-" if amount < 0 else ""
    whole, _, frac = f"{abs(amount):.{places}f}".partition(".")
    text = _group(whole, cur.grouping) + (f".{frac}" if frac else "")
    return f"{sign}{cur.symbol}{text}"


def format_minor(cents: int, code: str | None = None) -> str:
    """Format directly from integer minor units, skipping the float round-trip."""
    return format_money(cents / 100, code)


def rounding_step(dollar_step: int, code: str | None = None) -> int:
    """Scale a US-dollar-shaped rounding step into the current currency's major units."""
    return dollar_step * get(code).step


def scaled_minor(dollars: float, code: str | None = None) -> int:
    """A threshold written in US dollars, as minor units at this currency's scale.

    Detection cut-offs — "a large charge", "a costly subscription", "enough spending to call a
    trend" — are judgements about scale, not conversions. ₹75 is not the same kind of charge as
    $75, so a threshold left in raw minor units would flag a cup of coffee as an anomaly on a
    rupee ledger. These scale by the same per-currency factor as rounding steps.
    """
    return int(round(dollars * get(code).step * 100))


def catalog() -> list[dict]:
    """Currency options for the settings and sign-up pickers."""
    return [{"code": c.code, "symbol": c.symbol.strip(), "name": c.name, "locale": c.locale, "region": c.region}
            for c in CURRENCIES.values()]
