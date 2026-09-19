# Coin visuals (explanations as pictures)

Kids aged 5-8 often cannot read fluently, and several screens explain their concept in a sentence
or two. This replaces the pictureless parts (a bar, a "Vorher/Nachher" line, a duration in words)
with one shared vocabulary of countable things: coins, calendars, slots. The kid-sized lesson
sentence stays as the caption; the picture carries it, it does not replace it.

Mockups (approved by the owner, all screens below): `2026-09-19-coin-visuals-mockup.html`. Open it in a browser.

## Decisions (owner-approved)

- **Things you can count, not bars.** Money is coins, waiting is calendars, goal progress is slots.
  The Sparziele photo that fades from gray to color stays: it is a smooth change with no unit.
- **Vocabulary**
  - Big silver coin: your own money. Small coin: interest, gray if it comes from the Giro, gold if from a
    Festgeld. Dashed outline: not yours yet, or something you give up (the reward still to come,
    the interest lost by withdrawing, money that is about to arrive).
  - Calendar: one unit of waiting time. Slot: a tenth of a goal's price.
- **Pure CSS/HTML, no JS**, rendered by Jinja loops. Animation is CSS only, visual only, and off under
  `prefers-reduced-motion` like `.pop`/`.fall`. No sound.
- Printed amounts stay next to every picture so the numbers are still readable by a parent.
- Not in scope: celebration animations (Zinsen from a bank, Taschengeld from a parent) and the
  Dauerauftrag loop. They were not mocked; they stay in the backlog.

## The scale rule (one rule everywhere)

One coin is worth a **unit** picked from a fixed ladder so the largest stack in a scene never exceeds
a cap. The unit is printed once per scene ("1 Münze = 2 €").

- Big coins: ladder 1, 2, 5, 10, 20, 50, 100 € cap 10 coins.
- Small (interest) coins: ladder 5, 10, 25, 50 Cent, then 1, 2, 5, 10 €, cap 20 coins.
- A positive amount is never rounded to zero coins: it shows at least one (dashed if it is a
  not-yet amount). Round to nearest otherwise.
- All stacks in one scene share one unit, so stacks stay comparable (the three Festgeld offers do).
  A scene may have one big and one small unit (week card).

Time works the same way (`time_unit(days, cap=13)`): the unit is the smallest of a day, a week and a
month (30 days) that keeps the longest duration in the scene within 13 units. A fractional last unit is drawn smaller (never below about half
size, so a single spare day stays visible). All tiles in a scene share the unit, printed as
"1 Kalender = 1 Woche".

Paired stacks (stays/leaves on Abheben, stays/arrives on the transfer confirm, existing/arriving on
Einzahlen) come from `coins.split(total, part, unit) -> (rest, part)`, so they always add up to the coins
of one total; each positive share keeps at least one coin when the total has two or more.

Ceiling: the ladders stop at 100 € (big) and 10 € (small); a stack is clipped at `MAX_COINS = 20` and a
calendar row at `MAX_UNITS = 26`, and the printed amount carries the rest. Real pocket money is nowhere near it.

## Screens

| Screen | Today | New |
|---|---|---|
| Festgeld offers (`festgeld.html`, `_towers.html`, `_preview.html`) | 🗓️ plus "N Tage", two striped bars | Row of calendars for the term, Giro and Festgeld interest as small coin stacks with the amount below |
| Festgeld locked | 🔒, "Noch N Tage", preview sentence | Silver stack (deposit) with a dashed gold stack (the reward), a strip of units filling up, "Noch N Tage" |
| Festgeld ready | 🎁, big total, sentence | Silver stack and gold stack side by side, total, "Abholen!" |
| Abheben confirm (`cash.html`) | Vorher/Nachher line, interest sentence | Stack splits into "Bleibt" and "Geht raus"; a dashed interest coin hangs over what leaves. Vorher/Nachher line is dropped (the amounts sit under the stacks); `cash.out.interest*` sentences stay because they carry the number and period |
| Einzahlen confirm | Vorher/Nachher line | Same split scene: existing stack plus dashed arriving coins |
| Überweisen confirm (`transfer_confirm.html`) | Vorher/Nachher line | Three coins hop from your stack to the receiver's side (always three, the amount is in the stacks). The receiver side draws only the arriving coins, dashed, never the receiver's balance (privacy; a deliberate deviation from the mockup) |
| Home Giro card (`home.html`) | Interest sentence | Day pips counting to the next payout (unit from `time_unit(payout_days)`), ending in a coin with the expected cents; sentence stays |
| Home week card | Numbers in green/red | Coin rows per line (big unit for euros, small unit for interest), numbers stay |
| Sparziele bar (`_goal.html` `bar()`, used by home, goals, parent) | Percent bar | Ten slots, each a tenth of the price, the next one partly filled; "Ein Platz = X €". The goal picture stays where it was (the slots replace the bar in place; a deliberate deviation from the mockup). `pct` comes from the existing `goal_progress`. The kid sentence `goal.lesson` now reads "Die Plätze zeigen, wie nah du schon dran bist." (was "Der Balken zeigt...") |
| `err.insufficient` (transfer, Festgeld opening) | One sentence | Sentence plus two stacks, "Du hast" and "Du brauchst", the missing coins dashed |

## Code shape

- `app/coins.py` (leaf, no DB): `coin_unit(amounts, ladder, cap)`, `coins(cents, unit)` (count with the
  min-one rule), `time_unit(days, cap=13)`, `time_units(days, unit)` (float, for the smaller last calendar), `slot_fill(pct, n=10)`
  -> `(full, part_pct)`, `split(total, part, unit)`. Registered as Jinja globals beside `t` and the filters in `web.py`.
- `app/templates/_coins.html` macros: `stack(kind, n, ghost=0, tone='gray', row=False, label='')`,
  `calendars(units, label='')`, `pips(total, on, label='')`, `slots(pct, label='')` (the template macro that calls the
  Python `slot_fill`). The unit note is plain template text built from the `coin.unit`, `coin.unit.small` and
  `cal.unit.*` keys. `_towers.html` goes away; `_preview.html` keeps its out-of-band swap but renders the
  new stacks.
- `festgeld.offer_stacks(s, giro, cents) -> (products, view)` replaces `offer_towers`: `view` holds the shared coin unit,
  the shared calendar unit and, per offer id (`by_id`), coin counts and calendars instead of bar heights (`height()` is gone).
- `app/tailwind.css`: component classes for coin, stack, calendar, slot, hop animation. Rebuild
  and commit `app/static/app.css`.
- `routes/banking.py` passes the amounts the new scenes need. Routes that render `err.insufficient` add the
  have/need cents to the context.
- i18n keys (all through `t()`): unit notes ("1 Münze = {amount}", "1 Kalender = 1 Woche/1 Monat",
  "Ein Platz = {amount}"), "Bleibt", "Geht raus", "Du hast", "Du brauchst", aria labels. The
  `xfer.before_after` key is removed with its last user (the key-coverage test enforces this).
- Every picture has `role="img"` and an `aria-label` built from the printed amount.

## Testing

- `tests/test_coins.py`: ladder picks the right unit at each cap boundary, a positive amount is at
  least one coin, `time_units` fractions and `slot_fill` rounding (6.4 -> 6 full, 40 % part).
- `tests/test_app.py`: the affected pages render for a family fixture (offers, locked, ready, Abheben
  and Überweisen confirm, home with a goal). Assert on the printed unit note, not on markup details.
- Existing i18n coverage test must still pass; new keys must be used.

## Delivery order

1. Vocabulary (`coins.py`, macros, CSS) with Festgeld offers/locked/ready and Abheben confirm.
2. Überweisen/Einzahlen scene, home countdown, Sparziele slots.
3. Week card and the insufficient-funds picture.

Each step ships on its own with tests, rebuilt `app.css`, and `ruff` clean.

## Known limits

- Balances under 1 EUR are below the smallest big-coin unit (1 EUR), so a small remainder can draw as zero
  coins next to its printed amount.
- The `err.insufficient` picture is not shown when a cash withdrawal fails at the final PIN step because the
  balance changed in the meantime.
- A goal price of 1-4 cent would print "Ein Platz = 0,00 €".
