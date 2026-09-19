"""How many coins, calendars and slots a scene draws. Pure functions: no DB, no HTML.

One coin is worth a unit picked from a ladder so the biggest stack in a scene stays under a cap; the template
prints the unit once per scene. Above the top of a ladder the stack is clipped at MAX_COINS and the printed amount
carries the rest (real pocket money is nowhere near it)."""

BIG_LADDER = (100, 200, 500, 1000, 2000, 5000, 10000)  # cents per big (own money) coin: 1, 2, 5, 10, 20, 50, 100 EUR
BIG_CAP = 10
SMALL_LADDER = (5, 10, 25, 50, 100, 200, 500, 1000)  # cents per small (interest) coin
SMALL_CAP = 20
MAX_COINS = 20  # hard limit for one drawn stack
MAX_UNITS = 26  # hard limit for calendars in one row group


def coins(cents: int, unit: int) -> int:
    """Whole coins for an amount, nearest, halves up. A positive amount is never rounded down to nothing."""
    if cents <= 0:
        return 0
    return min(MAX_COINS, max(1, (cents + unit // 2) // unit))


def split(total: int, part: int, unit: int) -> tuple[int, int]:
    """(rest, part) coins for `total` cents split into `part` and the remainder: the two always add up to the coins of the total, and each positive share keeps at least one coin when the total has two or more."""
    whole = coins(total, unit)
    first = coins(part, unit)
    if part > 0 and whole >= 2:
        first = max(1, min(first, whole - (1 if total - part > 0 else 0)))
    return whole - first, first


def coin_unit(amounts, ladder: tuple[int, ...] = BIG_LADDER, cap: int = BIG_CAP) -> int:
    """Cents one coin is worth in a scene: the smallest ladder step at which the biggest amount fits in `cap` coins."""
    top = max(amounts, default=0)
    return next((u for u in ladder if max(1, (top + u // 2) // u) <= cap), ladder[-1])


def time_unit(days: int, cap: int = 13) -> int:
    """Days one calendar (or pip) stands for: a day, a week, or a month of 30 days, whichever keeps `days` within `cap` units."""
    return next((u for u in (1, 7) if days / u <= cap), 30)


def time_units(days: int, unit: int) -> float:
    """Calendars for a duration: whole ones plus the fraction of the last, uneven one (30 days in weeks = 4.29)."""
    return min(MAX_UNITS, round(days / unit, 2))


def slot_fill(pct: int, n: int = 10) -> tuple[int, int]:
    """(full slots, percent filled in the next one) for a goal at `pct` percent of its price, in `n` slots."""
    return divmod(pct * n, 100)


def shortfall(have: int, need: int) -> dict:
    """What the 'not enough money' picture draws: both stacks on one unit and the missing coins (at least one)."""
    unit = coin_unit([have, need])
    have_coins = coins(have, unit)
    return {"unit": unit, "have": have_coins, "gap": max(coins(need, unit) - have_coins, 1),
            "have_cents": have, "need_cents": need}
