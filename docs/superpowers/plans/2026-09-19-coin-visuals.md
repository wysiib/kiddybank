# Coin visuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace bars, "Vorher/Nachher" lines and plain-text durations with countable pictures (coins, calendars, slots) on the kid screens.

**Architecture:** A pure helper module `app/coins.py` decides how many coins/calendars/slots a scene draws (one unit ladder per scene). Jinja macros in `app/templates/_coins.html` render them as plain `<i>` elements styled by component classes in `app/tailwind.css`. Routes compute the counts and pass them in; templates only lay them out. No JS, no DB change.

**Tech Stack:** FastAPI, Jinja2, SQLModel, Tailwind v4 standalone (`bin/tailwindcss`), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-19-coin-visuals-design.md` (mockup: `docs/superpowers/specs/2026-09-19-coin-visuals-mockup.html`, open it in a browser to see every screen). Read `CLAUDE.md` first.

## Global Constraints

- All user-facing text goes through `t()` in `app/i18n.py`. `tests/test_i18n.py` fails on a missing key, an unused key, or a `{placeholder}` nobody fills (`name=` must appear in Python or template source).
- No sound, ever. Animation is CSS only and must be disabled under `prefers-reduced-motion` (extend the existing block at the bottom of `app/tailwind.css`).
- Every picture keeps its printed amount next to it; every picture has `role="img"` plus an `aria-label`, or `aria-hidden="true"` when the printed amount already sits beside it.
- Lesson sentences stay as captions. Only the duplicate "Vorher/Nachher" line goes.
- After touching templates or `app/tailwind.css` run `bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify` and commit `app/static/app.css` in the same commit. CI fails if it is stale.
- `uv run ruff check` and `uv run pytest` must pass at the end of every task.
- Kid routes must never reveal another user's balance. The transfer confirm screen therefore shows only the coins that arrive at the receiver, never the receiver's existing stack (this deliberately differs from the mockup, which drew Emma's own stack).
- Commit messages end with the two attribution lines from the session's system reminder (Co-Authored-By and Claude-Session).
- Ledger code is not touched. Amounts stay integer cents.

## File Structure

- Create `app/coins.py`: pure functions (units, counts, slot fill, shortfall). No DB, no HTML.
- Create `app/templates/_coins.html`: macros `stack`, `calendars`, `pips`, `slots`.
- Create `app/templates/_offer.html`, `_offer_unit.html`, `_short.html`: partials.
- Delete `app/templates/_towers.html`.
- Modify `app/tailwind.css` (component classes), `app/web.py` (one global, one filter), `app/festgeld.py` (`offer_stacks`), `app/routes/savings.py`, `app/routes/banking.py`, templates `festgeld.html`, `_preview.html`, `cash.html`, `transfer.html`, `transfer_confirm.html`, `home.html`, `_goal.html`, `goals.html`, `parent.html`, `app/i18n.py`.
- Tests: create `tests/test_coins.py`; extend `tests/test_app.py`.

New i18n keys (add as each task needs them, the task lists them): `coin.unit`, `coin.unit.small`, `cal.unit.1`, `cal.unit.7`, `cal.unit.30`, `fg.yours`, `fg.comes`, `fg.extra`, `cash.stays`, `cash.leaves`, `cash.after`, `xfer.stays`, `xfer.arrives`, `goal.slot`, `goal.pct`, `short.have`, `short.need`.

---

### Task 1: `coins.py`, the scale rule

**Files:**
- Create: `app/coins.py`
- Test: `tests/test_coins.py`

**Interfaces:**
- Produces (used by every later task):
  - constants `BIG_LADDER`, `BIG_CAP`, `SMALL_LADDER`, `SMALL_CAP`, `MAX_COINS`, `MAX_UNITS`
  - `coins(cents: int, unit: int) -> int`
  - `coin_unit(amounts, ladder=BIG_LADDER, cap=BIG_CAP) -> int`
  - `time_unit(days: int, cap: int = 13) -> int` (1, 7 or 30)
  - `time_units(days: int, unit: int) -> float`
  - `slot_fill(pct: int, n: int = 10) -> tuple[int, int]`
  - `shortfall(have: int, need: int) -> dict` with keys `unit`, `have` (coins), `gap` (missing coins, at least 1), `have_cents`, `need_cents`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coins.py`:

```python
from app import coins


def test_a_positive_amount_is_never_zero_coins():
    assert coins.coins(0, 100) == 0
    assert coins.coins(-5, 100) == 0
    assert coins.coins(3, 5) == 1  # 3 Cent still shows one small coin
    assert coins.coins(1, 100) == 1
    assert coins.coins(150, 100) == 2  # nearest, halves up
    assert coins.coins(149, 100) == 1


def test_one_stack_never_draws_more_than_the_cap():
    assert coins.coins(10**9, 100) == coins.MAX_COINS


def test_unit_is_the_smallest_ladder_step_that_fits_the_cap():
    assert coins.coin_unit([1000]) == 100  # 10 EUR = 10 coins of 1 EUR, exactly the cap
    assert coins.coin_unit([1100]) == 200  # 11 coins would exceed 10
    assert coins.coin_unit([1000, 5000]) == 500  # the biggest stack in the scene decides for all
    assert coins.coin_unit([]) == 100
    assert coins.coin_unit([10**9]) == coins.BIG_LADDER[-1]  # above the ladder: top step, the printed amount carries the rest


def test_small_ladder():
    assert coins.coin_unit([15], coins.SMALL_LADDER, coins.SMALL_CAP) == 5  # 3 coins
    assert coins.coin_unit([100], coins.SMALL_LADDER, coins.SMALL_CAP) == 5  # exactly 20 coins
    assert coins.coin_unit([110], coins.SMALL_LADDER, coins.SMALL_CAP) == 10


def test_time_unit_goes_day_week_month():
    assert coins.time_unit(7) == 1
    assert coins.time_unit(13) == 1
    assert coins.time_unit(14) == 7
    assert coins.time_unit(30) == 7
    assert coins.time_unit(91) == 7
    assert coins.time_unit(92) == 30
    assert coins.time_unit(365) == 30


def test_time_units_keep_the_fraction_of_the_last_calendar():
    assert coins.time_units(7, 7) == 1
    assert coins.time_units(30, 7) == 4.29  # 4 weeks and 2 days
    assert coins.time_units(90, 7) == 12.86
    assert coins.time_units(10**6, 30) == coins.MAX_UNITS


def test_slot_fill_is_whole_slots_plus_a_partly_filled_one():
    assert coins.slot_fill(64) == (6, 40)
    assert coins.slot_fill(0) == (0, 0)
    assert coins.slot_fill(100) == (10, 0)
    assert coins.slot_fill(33) == (3, 30)


def test_shortfall_always_shows_a_missing_coin():
    s = coins.shortfall(1000, 9999)
    assert (s["unit"], s["have"], s["gap"]) == (1000, 1, 9)
    close = coins.shortfall(149, 151)  # both round to the same count: still one dashed coin
    assert close["gap"] == 1
    assert (s["have_cents"], s["need_cents"]) == (1000, 9999)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_coins.py -v`
Expected: FAIL, `ImportError: cannot import name 'coins' from 'app'`.

- [ ] **Step 3: Write `app/coins.py`**

```python
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


def coin_unit(amounts, ladder: tuple[int, ...] = BIG_LADDER, cap: int = BIG_CAP) -> int:
    """Cents one coin is worth in a scene: the smallest ladder step at which the biggest amount fits in `cap` coins."""
    top = max(amounts, default=0)
    return next((u for u in ladder if coins(top, u) <= cap), ladder[-1])


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_coins.py -v && uv run ruff check`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add app/coins.py tests/test_coins.py
git commit -m "Add coins.py: one unit ladder decides how much each picture draws"
```

---

### Task 2: CSS, macros and template plumbing

**Files:**
- Modify: `app/tailwind.css`, `app/web.py`
- Create: `app/templates/_coins.html`
- Test: `tests/test_coins.py` (append)

**Interfaces:**
- Consumes: `coins.slot_fill` (Task 1).
- Produces:
  - Jinja global `slot_fill`; Jinja filter `compact` (cents -> "5 Cent" under a euro, else "1,50 €").
  - Macros in `_coins.html`, all return HTML:
    - `stack(kind, n, ghost=0, tone='gray', row=False, label='')`: `kind` is `'big'` or `'small'`; `tone` is `'gray'` or `'gold'` (small coins only); `n` solid coins then `ghost` dashed coins on top; `row=True` lays them out horizontally.
    - `calendars(units, label='')`: `units` may be a float; the fractional rest is drawn as a smaller last calendar.
    - `pips(total, on, label='')`: `total` round dots, the first `on` filled, dot number `on` (0-based) highlighted.
    - `slots(pct, label='')`: ten slots.
  - CSS classes: `stack stack-big stack-small stack-gold stack-row coin coin-ghost cals cal cal-part pips pip pip-on pip-now slots slot slot-on slot-part flight leave col`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coins.py`:

```python
from app import web


def render_macro(name, *args, **kw):
    return str(web.templates.env.get_template("_coins.html").module.__dict__[name](*args, **kw))


def test_stack_draws_solid_then_ghost_coins():
    html = render_macro("stack", "small", 3, ghost=2, tone="gold", label="15 Cent")
    assert html.count('<i class="coin"></i>') == 3 and html.count("coin-ghost") == 2
    assert "stack-gold" in html and 'aria-label="15 Cent"' in html
    assert "stack-row" in render_macro("stack", "big", 1, row=True)
    assert "aria-hidden" in render_macro("stack", "big", 1)  # no label: the printed amount beside it says it


def test_calendars_draw_a_smaller_last_one():
    html = render_macro("calendars", 4.29)
    assert html.count('<i class="cal"></i>') == 4 and html.count("cal-part") == 1
    assert "--k: 0.45" in html  # 0.29 of a week would vanish, so it is clamped to about half
    assert render_macro("calendars", 1).count("cal-part") == 0


def test_pips_mark_elapsed_and_current():
    html = render_macro("pips", 7, 3)
    assert html.count("pip-on") == 3 and html.count("pip-now") == 1 and html.count('<i class="pip') == 7


def test_slots_fill_partly():
    html = render_macro("slots", 64)
    assert html.count("slot-on") == 6 and html.count("slot-part") == 1 and "--fill: 40%" in html
    assert render_macro("slots", 100).count("slot-on") == 10
    assert web.templates.env.filters["compact"](5) == "5 Cent" and web.templates.env.filters["compact"](150) == "1,50 €"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_coins.py -v`
Expected: the four new tests FAIL (`TemplateNotFound: _coins.html`).

- [ ] **Step 3: Register the global and the filter in `app/web.py`**

Change the import block and the registrations:

```python
from . import coins, ledger
```
(replace `from . import ledger`), and after the existing `templates.env.filters["per"] = ...` line add:

```python
templates.env.globals["slot_fill"] = coins.slot_fill
templates.env.filters["compact"] = lambda cents: t("fg.cent", n=cents) if cents < 100 else format_money(cents)  # 5 -> "5 Cent", 150 -> "1,50 €"
```

- [ ] **Step 4: Create `app/templates/_coins.html`**

```jinja
{# Countable pictures (spec: docs/superpowers/specs/2026-09-19-coin-visuals-design.md). Pure HTML/CSS, no JS.
   Counts and units come from app/coins.py via the routes; nothing here decides how much to draw. #}

{# n solid coins, then `ghost` dashed ones on top (not yours yet / given up). kind: big (1 unit of own money) or small (interest). #}
{% macro stack(kind, n, ghost=0, tone='gray', row=False, label='') %}
<div class="stack stack-{{ kind }}{{ ' stack-gold' if tone == 'gold' }}{{ ' stack-row' if row }}"{% if label %} role="img" aria-label="{{ label }}"{% else %} aria-hidden="true"{% endif %}>
  {%- for _ in range(n) %}<i class="coin"></i>{% endfor -%}
  {%- for _ in range(ghost) %}<i class="coin coin-ghost"></i>{% endfor -%}
</div>
{% endmacro %}

{# One calendar per unit of waiting; a fractional `units` ends in a smaller calendar (never below about half size). #}
{% macro calendars(units, label='') %}
{% set full = units|int %}{% set rest = units - full %}
<div class="cals"{% if label %} role="img" aria-label="{{ label }}"{% else %} aria-hidden="true"{% endif %}>
  {%- for _ in range(full) %}<i class="cal"></i>{% endfor -%}
  {%- if rest > 0.02 %}<i class="cal cal-part" style="--k: {{ [rest, 0.45]|max }}"></i>{% endif -%}
</div>
{% endmacro %}

{# `total` dots, the first `on` filled, the next one highlighted (today). #}
{% macro pips(total, on, label='') %}
<div class="pips"{% if label %} role="img" aria-label="{{ label }}"{% else %} aria-hidden="true"{% endif %}>
  {%- for i in range(total) %}<i class="pip{{ ' pip-on' if i < on }}{{ ' pip-now' if i == on }}"></i>{% endfor -%}
</div>
{% endmacro %}

{# Ten slots, each a tenth of a goal's price; the next one is filled partly. #}
{% macro slots(pct, label='') %}
{% set fill = slot_fill(pct) %}
<div class="slots"{% if label %} role="img" aria-label="{{ label }}"{% else %} aria-hidden="true"{% endif %}>
  {%- for i in range(10) %}
  {%- if i < fill[0] %}<i class="slot slot-on"></i>
  {%- elif i == fill[0] and fill[1] %}<i class="slot slot-part" style="--fill: {{ fill[1] }}%"></i>
  {%- else %}<i class="slot"></i>{% endif %}
  {%- endfor -%}
</div>
{% endmacro %}
```

- [ ] **Step 5: Add the component classes to `app/tailwind.css`**

Inside the `@layer components { ... }` block, after `.input`, add (and delete nothing yet; the tower classes go in Task 3):

```css
  /* Countable pictures. A stack is a side view: .stack-big = 1 unit of own money (silver), .stack-small = interest (gray or gold). */
  .stack { @apply flex flex-col-reverse items-center; }
  .coin { display: block; border-radius: 9999px; border: 1.5px solid var(--color-slate-400); background: linear-gradient(var(--color-slate-200), var(--color-slate-300)); }
  .stack-big .coin { width: 2.5rem; height: 0.625rem; margin-top: -0.125rem; }
  .stack-small .coin { width: 1.375rem; height: 0.375rem; margin-top: -0.0625rem; }
  .stack-gold .coin { border-color: var(--color-amber-700); background: linear-gradient(var(--color-amber-200), var(--color-amber-400)); }
  .stack .coin-ghost { background: transparent; border-style: dashed; opacity: 0.65; }
  .stack-row { @apply flex-row gap-1; }
  .stack-row .coin { margin: 0; width: 1.6rem; height: 1.6rem; border-radius: 50%; }
  .stack-row.stack-small .coin { width: 0.9rem; height: 0.9rem; }
  .col { @apply flex flex-col items-center gap-1 text-center text-sm text-slate-500; }
  .col b { @apply text-base text-slate-800; }
  .leave { @apply rounded-2xl border-2 border-dashed border-slate-400 bg-white/60 px-2 pt-2 pb-1; }

  /* one calendar per unit of waiting, 7 per row; .cal-part is the smaller last one */
  .cals { display: grid; grid-template-columns: repeat(7, 0.75rem); gap: 0.125rem; justify-content: center; align-content: start; min-height: 1.75rem; }
  .cal { width: 0.75rem; height: 0.8125rem; border-radius: 3px; background: white; border: 1.5px solid var(--color-slate-400); border-top: 4px solid var(--color-pink-500); }
  .cal-part { transform: scale(var(--k)); transform-origin: left bottom; align-self: end; }

  /* dots counting up to a date */
  .pips { @apply flex flex-wrap justify-center gap-1; }
  .pip { @apply h-4 w-4 rounded-full border-2 border-slate-300 bg-slate-200; }
  .pip-on { border-color: var(--color-amber-700); background: var(--color-amber-400); }
  .pip-now { border-color: var(--color-pink-500); }

  /* goal progress: ten slots, each a tenth of the price */
  .slots { display: grid; grid-template-columns: repeat(10, 1fr); gap: 0.25rem; }
  .slot { aspect-ratio: 1; border-radius: 9999px; border: 2px dashed var(--color-slate-400); }
  .slot-on { border-style: solid; border-color: var(--color-amber-700); background: radial-gradient(circle at 35% 30%, var(--color-amber-200), var(--color-amber-400)); }
  .slot-part { border-style: solid; border-color: var(--color-amber-700); background: linear-gradient(to top, var(--color-amber-400) var(--fill), transparent var(--fill)); }

  /* coins hopping from one stack to another (transfer confirm) */
  .flight { position: relative; min-height: 6.25rem; }
  .flight .coin { position: absolute; left: 50%; bottom: 0.375rem; width: 2.5rem; height: 0.625rem; margin: 0 0 0 -1.25rem; animation: hop 2.4s ease-in-out infinite; }
  .flight .coin:nth-child(2) { animation-delay: 0.35s; }
  .flight .coin:nth-child(3) { animation-delay: 0.7s; }
```

Below the existing `@keyframes pop` line add:

```css
@keyframes hop {
  0% { transform: translate(-90px, 0); opacity: 0; }
  12% { opacity: 1; }
  50% { transform: translate(0, -46px); }
  88% { opacity: 1; }
  100% { transform: translate(90px, 0); opacity: 0; }
}
```

Replace the existing reduced-motion block with:

```css
@media (prefers-reduced-motion: reduce) {
  .fall, .shake, .pop { animation: none; }
  .flight .coin { animation: none; opacity: 1; }
  .flight .coin:nth-child(1) { transform: translate(-30px, -10px); }
  .flight .coin:nth-child(2) { transform: translate(0, -24px); }
  .flight .coin:nth-child(3) { transform: translate(30px, -10px); }
}
```

- [ ] **Step 6: Run tests, rebuild CSS**

```bash
uv run pytest tests/test_coins.py -v && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
```
Expected: PASS, ruff clean, CSS rebuilt without errors. (`test_i18n` still passes: `fg.cent` now appears in `web.py`.)

- [ ] **Step 7: Commit**

```bash
git add app/tailwind.css app/static/app.css app/web.py app/templates/_coins.html tests/test_coins.py
git commit -m "Add coin, calendar, pip and slot macros with their CSS"
```

---

### Task 3: Festgeld offers (term as calendars, interest as coins)

**Files:**
- Modify: `app/festgeld.py:1-8,64-75`, `app/routes/savings.py:34-58`, `app/templates/festgeld.html:31-45`, `app/templates/_preview.html`, `app/tailwind.css` (delete tower classes), `app/i18n.py`, `tests/test_app.py:142-150`
- Create: `app/templates/_offer.html`, `app/templates/_offer_unit.html`
- Delete: `app/templates/_towers.html`

**Interfaces:**
- Consumes: `coins.coin_unit`, `coins.coins`, `coins.time_unit`, `coins.time_units`, `SMALL_LADDER`, `SMALL_CAP` (Task 1); macros `stack`, `calendars` and filter `compact` (Task 2).
- Produces: `festgeld.offer_stacks(s, giro, cents) -> (products, view)` where `view = {"unit": int, "cal": int, "by_id": {product_id: {"giro": cents, "fg": cents, "giro_coins": int, "fg_coins": int, "cals": float}}}`. Templates get it as `view`. HTML ids: `offer-{product.id}` (tile body) and `offer-unit` (unit note).

- [ ] **Step 1: Update the existing test to the new ids (it fails first)**

In `tests/test_app.py`, in `test_parent_configures_rates_and_kid_opens_two_deposits`, replace lines 146-149 with:

```python
    assert "15 Cent" in page  # offers start from the 10 EUR demo amount: Kurz (1,5 % per week) pays 15 Cent, this kid's Giro 0
    bars = family.get("/festgeld/vorschau", params={"cents": 10_000, "product_id": 1}).text  # 100 EUR
    assert 'id="offer-1"' in bars and "1,50 €" in bars and "4 Cent" in bars  # Kurz 1,5 % for a week vs 0,04 % Giro
    assert f'id="offer-{turbo}"' in bars and "41 Cent" in bars  # Turbo 50 %/year for 3 days, and every tile is refreshed
    assert 'id="offer-unit"' in bars  # the unit note is refreshed too, the scale can change with the amount
```

Add this new test after it (before `def offers()` is inside the same test, so add as a separate test at the end of the festgeld tests, e.g. right after `test_festgeld_flow_and_module_switch`):

```python
def test_festgeld_offers_show_term_as_calendars_and_interest_as_coins(family):
    login(family, 2, "1111")
    page = family.get("/festgeld").text.split("Neue Schatztruhe")[1]
    assert page.count('class="cals"') == 3  # Kurz 7, Mittel 14, Lang 30 days
    assert "1 Kalender = 1 Woche" in page  # 30 days is more than 13 days, so the scene counts weeks
    assert "Kleine Münze = 10 Cent" in page  # unit printed once: Lang pays about 1,29 € on the 10 EUR demo, 13 coins of 10 Cent stay under the cap of 20
    assert page.count("cal-part") == 1  # Lang: 4 weeks and 2 days ends in a smaller calendar; 7 and 14 days are whole weeks
    assert "stack-gold" in page and "stack-small" in page
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py -k "festgeld_offers or parent_configures" -v`
Expected: FAIL (`offer-1` not found, no `cals`).

- [ ] **Step 3: `app/festgeld.py`**

Change the import to `from . import coins, ledger`. Replace the `TOWER_DEMO_CENTS` line and `offer_towers` function (lines 64-75) with:

```python
STACK_DEMO_CENTS = 1000  # amount the offer stacks show until the kid has dialled one in


def offer_stacks(s: Session, giro: Account, cents: int):
    """The offers plus what to draw for each: this Giro's and the offer's interest on `cents` as coins, and the
    term as calendars. Interest coins share one unit and terms share one calendar unit, so the offers compare."""
    products = s.exec(select(FestgeldProduct).order_by(FestgeldProduct.term_days)).all()
    cents = cents if cents > 0 else STACK_DEMO_CENTS
    bonus = {p.id: (ledger.interest_cents(cents, giro.interest_rate_bp, p.term_days), ledger.interest_cents(cents, p.rate_bp, p.term_days))
             for p in products}
    unit = coins.coin_unit([b for pair in bonus.values() for b in pair], coins.SMALL_LADDER, coins.SMALL_CAP)
    cal = coins.time_unit(max((p.term_days for p in products), default=1))
    by_id = {p.id: {"giro": g, "fg": f, "giro_coins": coins.coins(g, unit), "fg_coins": coins.coins(f, unit),
                    "cals": coins.time_units(p.term_days, cal)}
             for p in products for g, f in [bonus[p.id]]}
    return products, {"unit": unit, "cal": cal, "by_id": by_id}
```

- [ ] **Step 4: `app/routes/savings.py`**

Replace lines 39-40 and 54-55 area:

```python
    products, view = festgeld.offer_stacks(s, ledger.get_account(s, user.id, "giro"), 0)
    return render(request, "festgeld.html", user=user, rows=rows, products=products, view=view, error=error,
                  tok=issue_token(request, "festgeld"), days=ledger.get_account(s, user.id, "giro").payout_days)
```
and in `festgeld_preview`:
```python
    products, view = festgeld.offer_stacks(s, ledger.get_account(s, user.id, "giro"), cents)
    ctx = dict(oob=True, products=products, view=view)  # also refreshes the picture in every offer tile
```

- [ ] **Step 5: Templates**

Create `app/templates/_offer.html` (the body of one offer tile; `p` and `view` are in scope):

```jinja
{% from "_coins.html" import stack, calendars %}
{% set o = view.by_id[p.id] %}
{% set term = t('fg.days.1') if p.term_days == 1 else t('fg.days', n=p.term_days) %}
{{ calendars(o.cals, term) }}
<span class="text-base font-normal">{{ term }}</span>
<div class="flex min-h-24 items-end justify-center gap-3">
  <div class="col">{{ stack('small', o.giro_coins, label=o.giro|compact) }}<b>{{ o.giro|compact }}</b>{{ t('fg.tower.giro') }}</div>
  <div class="col">{{ stack('small', o.fg_coins, tone='gold', label=o.fg|compact) }}<b>{{ o.fg|compact }}</b>{{ t('fg.tower.fg') }}</div>
</div>
```

Create `app/templates/_offer_unit.html`:

```jinja
<p id="offer-unit" class="text-center text-sm text-slate-500"{% if oob %} hx-swap-oob="true"{% endif %}>{{ t('coin.unit.small', amount=view.unit|compact) }} · {{ t('cal.unit.' ~ view.cal) }}</p>
```

In `app/templates/festgeld.html` replace the tile body (lines 36-43: from `<label class="tile choice">` to `</label>`) with:

```jinja
      <label class="tile choice">
        <input type="radio" name="product_id" value="{{ p.id }}" class="sr-only" required>
        {{ p.name }}
        <div id="offer-{{ p.id }}" class="flex flex-col items-center gap-2">{% include "_offer.html" %}</div>
      </label>
```
and directly after the closing `</div>` of the `grid grid-cols-3` (before `</section>`) add:
```jinja
    {% if products %}{% include "_offer_unit.html" %}{% endif %}
```

`app/templates/_preview.html` becomes:

```jinja
<div class="lesson">
  {% if total %}{{ t('fg.preview', total=total|money) }}{% else %}{{ t('fg.preview.empty') }}{% endif %}
</div>
{% if oob %}{% for p in products %}<div id="offer-{{ p.id }}" class="flex flex-col items-center gap-2" hx-swap-oob="true">{% include "_offer.html" %}</div>{% endfor %}{% if products %}{% include "_offer_unit.html" %}{% endif %}{% endif %}
```

Delete the old file: `git rm app/templates/_towers.html`. In `app/tailwind.css` delete the three lines `.tower`, `.tower-giro`, `.tower-fg`.

- [ ] **Step 6: i18n keys** (`app/i18n.py`, in the festgeld block after `"fg.cent"`)

```python
        "coin.unit": "Große Münze = {amount}",
        "coin.unit.small": "Kleine Münze = {amount}",
        "cal.unit.1": "1 Kalender = 1 Tag",
        "cal.unit.7": "1 Kalender = 1 Woche",
        "cal.unit.30": "1 Kalender = 1 Monat",
```
(`cal.unit.` is used as a prefix, `t('cal.unit.' ~ view.cal)`; `test_i18n` allows that. `coin.unit` is used from Task 4 on; until then `test_no_key_is_left_over` flags it, so add `coin.unit` in Task 4 instead.) So add only `coin.unit.small` and the three `cal.unit.*` keys now.

- [ ] **Step 7: Run everything, rebuild CSS**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
```
Expected: all PASS. If `test_festgeld_offers...` fails on the unit note, print the page and check which unit was chosen. The defaults give 15 Cent for Kurz on the 10 EUR demo and roughly 1,29 € for Lang, so the small unit is 10 Cent; recompute with `coins.coin_unit([...], coins.SMALL_LADDER, coins.SMALL_CAP)` before touching the rule.

- [ ] **Step 8: Commit**

```bash
git add -A app tests
git commit -m "Show Festgeld offers as calendars for the wait and coins for the interest"
```

---

### Task 4: Festgeld locked and ready

**Files:**
- Modify: `app/routes/savings.py:34-41`, `app/templates/festgeld.html:8-28`, `app/i18n.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `coins.*` (Task 1), macros `stack`, `pips`, filter `compact` (Task 2).
- Produces: each row in the festgeld page context gains `big` (cents per big coin), `small` (cents per small coin), `deposit_coins`, `interest_coins`, `pips` = `(total_pips, elapsed_pips)`, `unit` (days per pip).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` after `test_festgeld_flow_and_module_switch`:

```python
def test_festgeld_locked_and_ready_show_stacks(family):
    login(family, 2, "1111")
    open_deposit(family, 1000, 1)  # 10 EUR, Kurz: 7 days, 15 Cent interest
    mine = lambda: family.get("/festgeld").text.split("Neue Schatztruhe")[0]  # noqa: E731  the deposit card, not the offers
    locked = mine()
    assert locked.count('<i class="coin"></i>') == 10 and locked.count("coin-ghost") == 3  # 10 EUR now, 3 x 5 Cent still to come
    assert 'class="pips"' in locked and "Große Münze = 1,00 €" in locked and "Kleine Münze = 5 Cent" in locked
    assert "Noch 7 Tage" in locked

    family.clock["today"] = D0 + timedelta(days=7)
    ready = mine()
    assert ready.count('<i class="coin"></i>') == 13 and "coin-ghost" not in ready  # the interest is solid gold now
    assert "Fertig!" in ready and "Abholen" in ready
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_festgeld_locked_and_ready_show_stacks -v`
Expected: FAIL (no coins in the deposit card).

- [ ] **Step 3: `app/routes/savings.py`**

Add `coins` to the import (`from .. import coins, festgeld, goals, ledger`). Add above `_festgeld_page`:

```python
def _deposit_row(a: Account, today: date) -> dict:
    """One open deposit with what its card draws: deposit and interest as coins, elapsed time as dots."""
    total, left = (a.maturity_date - a.opened_at).days, (a.maturity_date - today).days
    payout = festgeld.festgeld_payout(a)
    big = coins.coin_unit([a.balance_cents])
    small = coins.coin_unit([payout[1]], coins.SMALL_LADDER, coins.SMALL_CAP)
    unit = coins.time_unit(total)
    return {"acc": a, "status": festgeld.festgeld_status(a, today), "days": left, "payout": payout,
            "big": big, "small": small, "deposit_coins": coins.coins(a.balance_cents, big),
            "interest_coins": coins.coins(payout[1], small), "unit": unit,
            "pips": (-(-total // unit), max(0, total - left) // unit)}
```
and replace the `rows = [...]` statement in `_festgeld_page` with `rows = [_deposit_row(a, today) for a in deposits]`.

- [ ] **Step 4: `app/templates/festgeld.html`**

Add `{% from "_coins.html" import stack, pips %}` under the stepper import. Replace the `{% for r in rows %}...{% else %}` card block (lines 9-27) with:

```jinja
{% for r in rows %}
<div class="card space-y-3 text-center">
  <p class="text-base text-slate-500">{{ r.acc.name }} · {{ r.acc.interest_rate_bp|per(days) }} % {{ t('per.' ~ days) }}</p>
  <div class="flex items-end justify-center gap-4">
    <div class="col">{{ stack('big', r.deposit_coins, label=r.acc.balance_cents|money) }}<b>{{ r.acc.balance_cents|money }}</b>{{ t('fg.yours') }}</div>
    <div class="col">
      {% if r.status == 'ready' %}{{ stack('small', r.interest_coins, tone='gold', label=r.payout[1]|compact) }}
      {% else %}{{ stack('small', 0, ghost=r.interest_coins, tone='gold', label=r.payout[1]|compact) }}{% endif %}
      <b>+{{ r.payout[1]|compact }}</b>{{ t('fg.extra' if r.status == 'ready' else 'fg.comes') }}
    </div>
  </div>
  <p class="text-sm text-slate-500">{{ t('coin.unit', amount=r.big|compact) }} · {{ t('coin.unit.small', amount=r.small|compact) }}</p>
  {% if r.status == 'ready' %}
  <div class="pop text-7xl">🎁</div>
  <p class="text-2xl font-extrabold">{{ t('fg.ready') }}</p>
  <p class="text-5xl font-extrabold">{{ r.payout[0]|money }}</p>
  <p class="lesson">{{ t('fg.ready.lesson', interest=r.payout[1]|money) }}</p>
  <form method="post" action="/festgeld/{{ r.acc.id }}/abholen"><button class="btn w-full">🎉 {{ t('fg.collect') }}</button></form>
  {% else %}
  <div class="text-5xl">🔒</div>
  {{ pips(r.pips[0], r.pips[1]) }}
  <p class="text-sm text-slate-500">{{ t('cal.unit.' ~ r.unit) }}</p>
  <p class="text-2xl font-bold">⏳ {{ t('fg.locked.1') if r.days == 1 else t('fg.locked', n=r.days) }}</p>
  <p class="lesson">{{ t('fg.locked.lesson') }} {{ t('fg.preview', total=r.payout[0]|money) }}</p>
  {% endif %}
</div>
{% else %}
<p class="lesson">{{ t('fg.none') }}</p>
{% endfor %}
```
(The `{% else %}` before `<p class="lesson">{{ t('fg.none') }}` belongs to the `for`, as in the original. Keep the surrounding `{% if error %}` line unchanged.)

- [ ] **Step 5: i18n keys** (festgeld block)

```python
        "coin.unit": "Große Münze = {amount}",
        "fg.yours": "Deins",
        "fg.comes": "Kommt dazu",
        "fg.extra": "Extra fürs Warten",
```
(`fg.extra` is selected by a conditional expression `t('fg.extra' if ... else 'fg.comes')`. `test_no_key_is_left_over` looks for the literal `'fg.extra'` in source, which is present.)

- [ ] **Step 6: Run everything, rebuild CSS**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
```
Expected: all PASS. `test_festgeld_flow_and_module_switch` still expects `page.count("Noch 7 Tage")` semantics unchanged and "Fertig!".

- [ ] **Step 7: Commit**

```bash
git add -A app tests
git commit -m "Draw a locked Festgeld as deposit plus reward coins and a filling row of dots"
```

---

### Task 5: Abheben and Einzahlen confirm

**Files:**
- Modify: `app/routes/banking.py:144-152` (`_cash_page`), `app/templates/cash.html:19-33`, `app/i18n.py`, `tests/test_app.py:339-352`

**Interfaces:**
- Consumes: `coins.*` (Task 1), `stack` macro and `compact` filter (Task 2).
- Produces: `_cash_page` passes `pic` (only on the confirm step): `{"unit", "stay", "go", "lost", "small", "have", "come"}`; `cash.html` no longer uses `xfer.before_after` (the key stays until Task 6).

- [ ] **Step 1: Update the existing test (fails first)**

In `test_cash_in_and_out_need_parent_pin` replace the two `Vorher`/`Nachher` asserts:

```python
    r = family.post("/bar/abheben/pruefen", data={"cents": 300})
    assert "Bleibt auf dem Konto" in r.text and "Geht raus" in r.text and "7,00 €" in r.text
    assert r.text.count('<i class="coin"></i>') == 10 and r.text.count("coin-ghost") == 1  # 7 stay, 3 leave, 1 interest coin lost
    assert "Große Münze = 1,00 €" in r.text
    assert "verpasst du" in r.text and "Zinsen" in r.text  # withdrawing costs interest, says by how much
```
(the existing `assert "verpasst du"...` line stays; delete the old `Vorher` line only), and:

```python
    r = family.post("/bar/einzahlen/pruefen", data={"cents": 500})
    assert "Danach hast du 12,00 €" in r.text and r.text.count("coin-ghost") == 5  # 5 dashed coins arrive
```
in place of the `"Nachher 12,00 €"` assert (the balance is 7,00 € at that point, so 12 coins in total: unit is 2 EUR? see Step 4 note).

Note on the arithmetic: at that point in the test the balance is 700 and 500 arrives, total 1200, so `coin_unit([700, 1200])` is 200 (1200/100 = 12 > cap 10). Coins: have `coins(700, 200)` = 4 (3.5 rounds up), arriving `coins(500, 200)` = 3 (2.5 rounds up). So use `r.text.count("coin-ghost") == 3` and `Große Münze = 2,00 €`. Write the assert that way:

```python
    r = family.post("/bar/einzahlen/pruefen", data={"cents": 500})
    assert "Danach hast du 12,00 €" in r.text and r.text.count("coin-ghost") == 3 and "Große Münze = 2,00 €" in r.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_cash_in_and_out_need_parent_pin -v`
Expected: FAIL.

- [ ] **Step 3: `_cash_page` in `app/routes/banking.py`**

Add `coins` to the import (`from .. import auth, coins, events, goals, ledger`) and replace `_cash_page`:

```python
def _cash_page(request: Request, s: Session, user: User, kind: str, cents: int | None = None, error: str | None = None):
    if kind not in CASH:
        raise HTTPException(404)
    giro = ledger.get_account(s, user.id, "giro")
    lost = ledger.interest_cents(cents or 0, giro.interest_rate_bp, giro.payout_days) if kind == "abheben" else 0
    pic = None
    if cents:  # the confirm step draws what stays, what moves and what interest is given up
        after = giro.balance_cents + (cents if kind == "einzahlen" else -cents)
        unit = coins.coin_unit([giro.balance_cents, after])
        small = coins.coin_unit([lost], coins.SMALL_LADDER, coins.SMALL_CAP)
        pic = {"unit": unit, "small": small, "after": after, "lost": coins.coins(lost, small),
               "stay": coins.coins(after, unit), "go": coins.coins(cents, unit),
               "have": coins.coins(giro.balance_cents, unit), "come": coins.coins(cents, unit)}
    return render(request, "cash.html", user=user, giro=giro, kind=kind, cents=cents, pic=pic,
                  lost=lost, error=error, tok=issue_token(request, "cash") if cents else None)
```

- [ ] **Step 4: `app/templates/cash.html`**

Add `{% from "_coins.html" import stack %}` under the other imports. In the confirm branch replace the `xfer.before_after` line (line 21) with:

```jinja
  {% if out %}
  <div class="grid grid-cols-2 items-end gap-3">
    <div class="col">{{ stack('big', pic.stay, label=pic.after|money) }}<b>{{ pic.after|money }}</b>{{ t('cash.stays') }}</div>
    <div class="leave col">
      {% if lost %}{{ stack('small', 0, ghost=pic.lost, tone='gold', label=lost|compact) }}{% endif %}
      {{ stack('big', pic.go, label=cents|money) }}<b>{{ cents|money }}</b>{{ t('cash.leaves') }}
    </div>
  </div>
  {% else %}
  <div class="flex justify-center"><div class="col">{{ stack('big', pic.have, ghost=pic.come, label=pic.after|money) }}<b>{{ t('cash.after', amount=pic.after|money) }}</b></div></div>
  {% endif %}
  <p class="text-sm text-slate-500">{{ t('coin.unit', amount=pic.unit|compact) }}{% if out and lost %} · {{ t('coin.unit.small', amount=pic.small|compact) }}{% endif %}</p>
```

- [ ] **Step 5: i18n keys**

```python
        "cash.stays": "Bleibt auf dem Konto",
        "cash.leaves": "Geht raus",
        "cash.after": "Danach hast du {amount}",
```

- [ ] **Step 6: Run everything, rebuild CSS, commit**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add -A app tests
git commit -m "Show Abheben as a stack that splits, with the interest coin you give up"
```
Expected: all PASS. If a coin count differs by one, the cause is rounding at half coins (`coins()` rounds halves up); recompute with `python -c "from app import coins; print(coins.coins(700,200))"` and fix the assertion, not the rule.

---

### Task 6: Überweisen confirm, and the end of `xfer.before_after`

**Files:**
- Modify: `app/routes/banking.py:105-119` (`transfer_check`), `app/templates/transfer_confirm.html`, `app/i18n.py`, `tests/test_app.py:78-93`

**Interfaces:**
- Consumes: `coins.*`, `stack` macro, `compact`/`money` filters.
- Produces: `transfer_confirm.html` context gains `pic = {"unit", "mine", "go"}`. Removes the i18n key `xfer.before_after` (last user).

- [ ] **Step 1: Update the test (fails first)**

In `test_transfer_confirm_and_insufficient`, replace the `Vorher`/`Nachher` line with:

```python
    r = family.post("/ueberweisen/pruefen", data={**form, "cents": 300})
    assert "Bleibt bei dir" in r.text and "7,00 €" in r.text and "Kommt bei Mama an" in r.text
    assert r.text.count("coin-ghost") == 3  # the 3 EUR arrive as dashed coins at the receiver
```
Add a new test after it:

```python
def test_transfer_confirm_does_not_leak_the_receivers_balance(family):
    with Session(web._engine()) as s:
        ledger.get_account(s, 1, "giro").balance_cents = 4242
        s.commit()
    login(family, 2, "1111")
    r = family.post("/ueberweisen/pruefen", data={"to_id": account_id(1, "giro"), "cents": 300})
    assert "42,42" not in r.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py -k transfer -v`
Expected: FAIL on the new asserts.

- [ ] **Step 3: `transfer_check`**

Replace its final `return render(...)`:

```python
    unit = coins.coin_unit([src.balance_cents])  # only the sender's own balance sets the scale, never the receiver's
    pic = {"unit": unit, "mine": coins.coins(src.balance_cents - cents, unit), "go": coins.coins(cents, unit)}
    return render(request, "transfer_confirm.html", user=user, src=src, dst=dst, cents=cents, pic=pic,
                  to_name=s.get(User, dst.user_id).name, tok=issue_token(request, "transfer"))
```

- [ ] **Step 4: `app/templates/transfer_confirm.html`**

Replace the whole card (`<div class="card space-y-4 text-center">` … `</div>`) with:

```jinja
{% from "_coins.html" import stack %}
<div class="card space-y-4 text-center">
  <p class="text-3xl font-extrabold">💸 {{ t('xfer.confirm', amount=cents|money, name=to_name) }}</p>
  <div class="grid grid-cols-3 items-end">
    <div class="col">{{ stack('big', pic.mine, label=(src.balance_cents - cents)|money) }}<span class="text-3xl">👛</span><b>{{ (src.balance_cents - cents)|money }}</b>{{ t('xfer.stays') }}</div>
    <div class="flight" aria-hidden="true"><i class="coin"></i><i class="coin"></i><i class="coin"></i></div>
    <div class="col">{{ stack('big', 0, ghost=pic.go, label=cents|money) }}<span class="text-3xl">➡️</span><b>{{ cents|money }}</b>{{ t('xfer.arrives', name=to_name) }}</div>
  </div>
  <p class="text-sm text-slate-500">{{ t('coin.unit', amount=pic.unit|compact) }}</p>
  <p class="lesson">{{ t('xfer.lesson') }}</p>
</div>
```
(Put the `{% from ... %}` line right after `{% extends "base.html" %}` instead if Jinja complains about an import inside a block; either placement works for imports at template level, top is the convention here.)

- [ ] **Step 5: i18n**

Remove `"xfer.before_after"`. Add:

```python
        "xfer.stays": "Bleibt bei dir",
        "xfer.arrives": "Kommt bei {name} an",
```

- [ ] **Step 6: Run everything, rebuild CSS, commit**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add -A app tests
git commit -m "Let three coins hop on the transfer confirm and drop the Vorher/Nachher line"
```
Expected: all PASS (`test_i18n` proves `xfer.before_after` has no user left).

---

### Task 7: Home interest countdown

**Files:**
- Modify: `app/routes/banking.py:38-54`, `app/templates/home.html:5-15`, `tests/test_app.py`

**Interfaces:**
- Consumes: `coins.time_unit`, `pips` macro, `compact` filter, existing `ledger.next_interest`.
- Produces: `home.html` gets `payout` = `{"total": int, "on": int, "cents": int, "unit": int}` or `None` (None exactly when `interest_hint` is empty).

- [ ] **Step 1: Write the failing test**

```python
def test_home_counts_down_to_the_interest_payout(family):
    login(family, 2, "1111")  # 10 EUR at 1 % per week, paid every 7 days: 10 Cent
    home = family.get("/home").text
    assert home.count('class="pip"') == 6 and home.count("pip-now") == 1 and "pip-on" not in home
    assert "10 Cent" in home
    family.clock["today"] = D0 + timedelta(days=3)
    home = family.get("/home").text
    assert home.count("pip-on") == 3  # three of seven days are over
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_home_counts_down_to_the_interest_payout -v`
Expected: FAIL.

- [ ] **Step 3: `app/routes/banking.py`**

Replace `_interest_hint` with a function returning both (keep the empty-payout rule):

```python
def _interest_hint(acc: Account, today: date) -> tuple[str, dict | None]:
    """(sentence, dots picture). Both empty when the payout would round to 0 cents: nothing gets booked then, so don't promise it."""
    days, cents = ledger.next_interest(acc, today)
    if not cents:
        return "", None
    when = t("home.interest.tomorrow") if days == 1 else t("home.interest.days", days=days)
    unit = coins.time_unit(acc.payout_days)
    total = -(-acc.payout_days // unit)
    return t("home.interest", when=when, amount=format_money(cents)), \
        {"total": total, "on": max(0, total - -(-days // unit)), "cents": cents, "unit": unit}
```
In `home()`: `hint, payout = _interest_hint(giro, today)` before the return; pass `interest_hint=hint, payout=payout`.

- [ ] **Step 4: `app/templates/home.html`**

Add `{% from "_coins.html" import pips %}` to the imports. After the balance div (`<div class="text-5xl ...">`) and before the `{% if interest_hint %}` line insert:

```jinja
  {% if payout %}
  <div class="flex items-center gap-3">
    {{ pips(payout.total, payout.on, interest_hint) }}
    <span class="text-2xl">→</span>
    <span class="rounded-full border-4 border-amber-700 bg-amber-400 px-3 py-1 text-lg font-extrabold text-amber-900">{{ payout.cents|compact }}</span>
  </div>
  <p class="text-sm text-slate-500">{{ t('cal.unit.' ~ payout.unit) }}</p>
  {% endif %}
```

- [ ] **Step 5: Run everything, rebuild CSS, commit**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add -A app tests
git commit -m "Count down the days to the next interest payout with dots"
```
Expected: all PASS.

---

### Task 8: Sparziele as ten slots

**Files:**
- Modify: `app/templates/_goal.html`, `app/templates/home.html:31-38`, `app/templates/goals.html:13`, `app/templates/parent.html:58`, `app/i18n.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `slots` macro (Task 2), `goal_progress` percent (unchanged).
- Produces: macro `progress(pct, target_cents)` in `_goal.html`, replacing `bar(pct)`. The goal picture (photo fade or emoji) is unchanged and stays where it is in each layout (the mockup put it at the end of the row; keeping the existing layout avoids reshuffling three screens).

- [ ] **Step 1: Write the failing test**

```python
def test_goal_progress_is_ten_slots(family):
    login(family, 2, "1111")  # 10 EUR in the Giro
    add_goal(family, name="Fahrrad", cents=3000)  # 33 % of 30 EUR
    page = family.get("/ziele").text
    assert page.count('class="slot slot-on"') == 3 and page.count("slot-part") == 1 and "--fill: 30%" in page
    assert "Ein Platz = 3,00 €" in page
    assert "slot-on" in family.get("/home").text
```
Check the signature of the existing `add_goal` helper (`grep -n "def add_goal" tests/test_app.py`) and pass `cents=3000` in the form of its keyword arguments; adapt the call to it (it posts `name`, `cents`).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_goal_progress_is_ten_slots -v`
Expected: FAIL.

- [ ] **Step 3: `app/templates/_goal.html`**

Replace the `bar` macro (lines 7-9) and the comment on line 1 ("progress bar" → "progress slots"):

```jinja
{% from "_coins.html" import slots %}
{% macro progress(pct, target_cents) %}
{{ slots(pct, t('goal.pct', pct=pct)) }}
<p class="text-center text-sm text-slate-500">{{ t('goal.slot', amount=((target_cents + 5) // 10)|money) }}</p>
{% endmacro %}
```
(Jinja macros in a file may be preceded by an import at top level; put the `{% from %}` as the first line.) Add i18n key `"goal.pct": "{pct} von 100 Prozent geschafft"` (used as aria-label) and `"goal.slot": "Ein Platz = {amount}"`.

- [ ] **Step 4: Call sites**

- `home.html`: import line `{% from "_goal.html" import visual, progress %}`; `{{ bar(top.pct) }}` → `{{ progress(top.pct, top.goal.target_cents) }}`.
- `goals.html`: import `visual, progress`; `{{ bar(c.pct) }}` → `{{ progress(c.pct, c.goal.target_cents) }}`.
- `parent.html`: find the import of `bar` (`grep -n "bar" app/templates/parent.html`), change to `progress`, and `{{ bar(c.pct) }}` → `{{ progress(c.pct, c.goal.target_cents) }}`.

- [ ] **Step 5: Run everything, rebuild CSS, commit**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add -A app tests
git commit -m "Show Sparziele progress as ten coin slots instead of a bar"
```
Expected: all PASS (the existing goal tests assert text like "Noch 10,00 € bis dahin", which is untouched).

---

### Task 9: Week card as coin rows

**Files:**
- Modify: `app/routes/banking.py` (`home`), `app/templates/home.html:17-28`, `app/i18n.py`, `tests/test_app.py:309-322`

**Interfaces:**
- Consumes: `coins.*`, `stack` macro, `compact`.
- Produces: `home.html` context gains `week_pic = {"big": cents, "small": cents, "coins": {kind: count}}`; `week` (dict of cents) is unchanged.

- [ ] **Step 1: Extend the test**

In `test_home_week_card`, after `assert "-3,00 €" in family.get("/home").text` add:

```python
    card = family.get("/home").text
    assert card.count("stack-row") == 2 and "Große Münze = 1,00 €" in card  # 10 EUR from others, 3 EUR spent: coins in a row each
```
and after the `later = ...` assertions add `assert "stack-small" in later` (the interest row uses small coins).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_home_week_card -v`
Expected: FAIL.

- [ ] **Step 3: `home()` in `app/routes/banking.py`**

After `week = {...}` add:

```python
    euros = [v for k, v in week.items() if k != "zins"]
    big = coins.coin_unit(euros)
    small = coins.coin_unit([week.get("zins", 0)], coins.SMALL_LADDER, coins.SMALL_CAP)
    week_pic = {"big": big, "small": small,
                "coins": {k: coins.coins(v, small if k == "zins" else big) for k, v in week.items()}}
```
and pass `week_pic=week_pic` to `render`.

- [ ] **Step 4: `home.html` week card**

Add `stack` to the coins import (`{% from "_coins.html" import pips, stack %}`). Replace the loop body:

```jinja
  {% for k, cents in week.items() %}
  <div class="grid grid-cols-[1fr_auto] items-center gap-2">
    <div>
      <span>{{ t('week.' ~ k) }}</span>
      {% if k == 'zins' %}{{ stack('small', week_pic.coins[k], tone='gold', row=True, label=cents|money) }}
      {% else %}{{ stack('big', week_pic.coins[k], row=True, label=cents|money) }}{% endif %}
    </div>
    <span class="text-2xl font-extrabold {{ 'text-red-600' if k == 'spent' else 'text-green-600' }}">{{ '-' if k == 'spent' else '+' }}{{ cents|money }}</span>
  </div>
  {% endfor %}
  <p class="text-sm text-slate-500">{{ t('coin.unit', amount=week_pic.big|compact) }}{% if week.get('zins') %} · {{ t('coin.unit.small', amount=week_pic.small|compact) }}{% endif %}</p>
```
No new i18n key is needed here (`coin.unit` and `coin.unit.small` already exist).

- [ ] **Step 5: Run everything, rebuild CSS, commit**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add -A app tests
git commit -m "Draw the week card as rows of coins"
```

---

### Task 10: "Not enough money" pictures

**Files:**
- Create: `app/templates/_short.html`
- Modify: `app/routes/banking.py` (`_transfer_form`, `transfer_check`, `transfer_do`, `_cash_page`, `cash_check`), `app/routes/savings.py` (`_festgeld_page`, `festgeld_open`), `app/templates/transfer.html`, `app/templates/cash.html`, `app/templates/festgeld.html`, `app/i18n.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `coins.shortfall` (Task 1), `stack`, `money`, `compact`.
- Produces: templates accept an optional `gap` context value (the dict from `coins.shortfall`); `{% if gap %}{% include "_short.html" %}{% endif %}` goes directly after each `{% if error %}...{% endif %}` line.

- [ ] **Step 1: Write the failing test**

In `test_transfer_confirm_and_insufficient`, after the `9999` request assert:

```python
    assert "Du hast 10,00 €" in r.text and "Du brauchst 99,99 €" in r.text and r.text.count("coin-ghost") == 9
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_app.py::test_transfer_confirm_and_insufficient -v`
Expected: FAIL.

- [ ] **Step 3: The partial**

Create `app/templates/_short.html`:

```jinja
{% from "_coins.html" import stack %}
<div class="card space-y-2 text-center">
  <div class="flex items-end justify-center gap-8">
    <div class="col">{{ stack('big', gap.have, label=gap.have_cents|money) }}<b>{{ t('short.have', amount=gap.have_cents|money) }}</b></div>
    <div class="col">{{ stack('big', gap.have, ghost=gap.gap, label=gap.need_cents|money) }}<b>{{ t('short.need', amount=gap.need_cents|money) }}</b></div>
  </div>
  <p class="text-sm text-slate-500">{{ t('coin.unit', amount=gap.unit|compact) }}</p>
</div>
```
i18n: `"short.have": "Du hast {amount}"`, `"short.need": "Du brauchst {amount}"`.

- [ ] **Step 4: Routes**

`app/routes/banking.py`:
- `_transfer_form(request, s, user, error=None, gap=None)`: pass `gap=gap` to `render`.
- `transfer_check`: in the `cents > src.balance_cents` branch keep raising; in the `except LedgerError as e` return `_transfer_form(request, s, user, e.args[0], gap=coins.shortfall(src.balance_cents, cents) if e.args[0] == "err.insufficient" else None)`.
- `transfer_do`: same expression in its `except`.
- `_cash_page(..., error=None, gap=None)`: pass `gap=gap`. In `cash_check`: `return _cash_page(request, s, user, kind, error="err.insufficient", gap=coins.shortfall(giro.balance_cents, cents))`.

`app/routes/savings.py`: `_festgeld_page(..., error=None, gap=None)` passes `gap=gap`; in `festgeld_open`'s `except LedgerError as e:` use `gap=coins.shortfall(ledger.get_account(s, user.id, "giro").balance_cents, cents) if e.args[0] == "err.insufficient" else None`.

- [ ] **Step 5: Templates**

Add `{% if gap %}{% include "_short.html" %}{% endif %}` right after the `{% if error %}<p class="error">…</p>{% endif %}` line in `transfer.html`, `cash.html` (the one at the top of the block) and `festgeld.html`.

- [ ] **Step 6: Run everything, rebuild CSS, commit**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add -A app tests
git commit -m "Show what is missing as dashed coins when the Giro is too low"
```

---

### Task 11: Docs and final check

**Files:**
- Modify: `CLAUDE.md`, `docs/ideas.md`, `docs/superpowers/specs/2026-09-19-coin-visuals-design.md`

- [ ] **Step 1: Bring the spec in line with what shipped**

In the spec's scale-rule section replace the sentence about time ("up to 31 days a unit is a day, up to 91 days a week, beyond that a month of 30 days") with: "the unit is the smallest of a day, a week and a month (30 days) that keeps the longest duration in the scene within 13 units". Replace `slots(pct)` with `slot_fill(pct)` in the Code shape section, and note that the Sparziele goal picture keeps its old place and the transfer confirm draws only arriving coins, never the receiver's balance.

- [ ] **Step 2: `CLAUDE.md`**

In the **Frontend** paragraph add: "Concept explanations are pictures, not bars: `coins.py` picks how many coins, calendars or slots a scene draws (one unit ladder per scene, printed as 'Große Münze = 1,00 €'), `_coins.html` renders them as plain `<i>` elements, classes live in `app/tailwind.css`. Silver big coin = own money, small coin = interest (gray Giro, gold Festgeld), dashed = not yours yet or given up. Never draw another user's balance. Spec: `docs/superpowers/specs/2026-09-19-coin-visuals-design.md`." Also add `coins.py` to the leaf-helper list in the Layering sentence (`i18n.py` and `auth.py` are leaf helpers -> "`i18n.py`, `auth.py` and `coins.py`").

- [ ] **Step 3: `docs/ideas.md`**

Change the "In progress: **Coin visuals**" line to "Done: **Coin visuals**". Keep the sentence about celebration animations and the Dauerauftrag loop staying in the backlog.

- [ ] **Step 4: Full check**

```bash
uv run pytest && uv run ruff check
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify && git status --short
```
Expected: tests pass, ruff clean, and `git status` shows `app/static/app.css` unchanged after the rebuild (proves it was current).

- [ ] **Step 5: Run the app and look**

Run `uv run uvicorn app.main:app --reload`, create a parent and a kid, then open in a browser: `/festgeld` (offers, then open a deposit), `/bar/abheben` with an amount, `/ueberweisen` confirm, `/home` (dots, week card), `/ziele` (slots). Compare against the mockup file. Report any screen that looks off instead of fixing it silently.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs
git commit -m "Document the coin visuals"
```

---

## Self-review notes (already applied above)

- Spec coverage: scale rule (Task 1), vocabulary and CSS (Task 2), Festgeld offers/locked/ready (3, 4), Abheben + Einzahlen (5), Überweisen (6), home countdown (7), Sparziele (8), week card (9), insufficient (10), docs (11). Celebrations and the Dauerauftrag loop are out of scope by the spec.
- Two deliberate deviations from the mockup, both recorded in the spec update in Task 11: no receiver balance on the transfer confirm (privacy), goal picture keeps its current place.
- Names used across tasks: `offer_stacks`, `view.by_id`, `pic`, `gap`, `payout`, `week_pic`, filter `compact`, macros `stack/calendars/pips/slots/progress`. Time unit is chosen by `time_unit(days, cap=13)` everywhere.
