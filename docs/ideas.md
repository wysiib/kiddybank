# Feature backlog

Ideas from the 2026-09-19 review, not yet designed. Pick one, then brainstorm it the same way as
Sparziele (see `docs/superpowers/specs/`). Check each against the product constraints in `CLAUDE.md`
(kid-sized lesson sentence, no sound, no Spar account, rates parent-configurable, all text via `t()`).

Done: **PIN lockout** (five wrong PINs lock a user for five minutes, login and cash desk), **Sparziele** (spec and plan in `docs/superpowers/`), **Einzahlen/Abheben** (kid picks an amount, a parent confirms with their PIN), **PIN change** (kid changes their own PIN with the old one at `/pin`; a parent resets a kid's PIN in `/eltern`, which also lifts a lockout). Still open: a parent who forgets their own PIN has no recovery path.

Rejected: **Geld-Wunsch an Mama/Papa**. A real bank doesn't take wishes for money, so we don't teach that.

## Training wheels

- ~~**Wochen-Rückblick (home card).**~~ Done: rolling 7 days, `ledger.week_summary`, hidden when the week is empty.
- **"Kann ich mir das leisten?" on transfer.** Show what is left after the transfer and warn when it
  uses most of the balance. Extends the existing before/after line.
- **Zinsen-Vorschau on the Giro.** "Wenn du 1 Jahr nichts ausgibst: +X €", or a small growth chart.
  Shows compounding and waiting.

## Better understanding

- **Lesson on old statement lines.** Tap a line for a one-sentence explanation ("Zinsen: die Bank
  bezahlt dich fürs Warten"). Celebrations do this now, statements do not.
- **Festgeld countdown on the home tile.** "noch 3 Tage 🔒" or 🎁 when ready, so Abholen is hard to miss.
- ~~**Parent view of a kid's statement.**~~ Done: `/eltern/kinder/{uid}/konto` (Giro only, reuses `statement.html`).
- **Kid-facing stocks.** `market.py` and the tables exist and are tested; only the UI is missing.
  Lesson: prices can go down. Gate on `stocks_enabled`, probably for the 7-8 year olds.

## System

- **Backup/export.** Parent-only "Download backup" (SQLite `.backup`). Everything lives in `kiddybank.db`.
  Note: Sparziele photos are stored in the DB, so they are covered too. Does not touch the deployment milestone.
- **Undo for parent bookings.** One reversal `Transaction` instead of a manual counter-booking after a typo.

## Sparziele follow-ups (deliberately left out of v1)

- **Time estimate.** "In ca. 6 Wochen bei deinem Taschengeld", derived from `RecurringRule` and the giro rate.
- Money earmarking, shared/family goals, image cropping, non-JPEG photos, parent approval of goals.

## Suggested order

Wochen-Rückblick and the parent statement view first (most visible, existing data only), then the
backup.
