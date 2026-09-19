# Feature backlog

Ideas from the 2026-09-19 review, not yet designed. Pick one, then brainstorm it the same way as
Sparziele (see `docs/superpowers/specs/`). Check each against the product constraints in `CLAUDE.md`
(kid-sized lesson sentence, no sound, no Spar account, rates parent-configurable, all text via `t()`).

Done: **Sparziele** (spec and plan in `docs/superpowers/`).

## Training wheels

- **Wochen-Rückblick (home card).** "Diese Woche: +2,00 € Taschengeld, +0,04 € Zinsen, -1,50 € ausgegeben."
  Teaches income vs. spending. Derived from existing `Transaction` rows, no schema change.
- **"Kann ich mir das leisten?" on transfer.** Show what is left after the transfer and warn when it
  uses most of the balance. Extends the existing before/after line.
- **Geld-Wunsch an Mama/Papa.** Kid asks a parent for money with a reason; the parent approves or
  declines. Teaches that money is not automatic. Needs an approval flow (could also serve Sparziele purchases later).
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
- **PIN lockout.** Delay after 5 wrong PINs; a 4-digit PIN is brute-forceable in minutes.
- **Undo for parent bookings.** One reversal `Transaction` instead of a manual counter-booking after a typo.

## Sparziele follow-ups (deliberately left out of v1)

- **Time estimate.** "In ca. 6 Wochen bei deinem Taschengeld", derived from `RecurringRule` and the giro rate.
- Money earmarking, shared/family goals, image cropping, non-JPEG photos, parent approval of goals.

## Suggested order

Wochen-Rückblick and the parent statement view first (most visible, existing data only), then the
hardening pair (backup, PIN lockout).
