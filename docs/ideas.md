# Feature backlog

Ideas from the 2026-09-19 review, not yet designed. Pick one, then brainstorm it the same way as
savings goals (see `docs/superpowers/specs/`). Check each against the product constraints in `CLAUDE.md`
(kid-sized lesson sentence, no sound, no savings account, rates parent-configurable, all text via `t()`).

Done: **PIN lockout** (five wrong PINs lock a user for five minutes, login and cash desk), **Savings goals** (spec and plan in `docs/superpowers/`), **Cash deposit/withdrawal** (kid picks an amount, a parent confirms with their PIN), **PIN change** (kid changes their own PIN with the old one at `/pin`; a parent resets a kid's PIN in `/parent`, which also lifts a lockout). Still open: a parent who forgets their own PIN has no recovery path.

Rejected: **Asking mom/dad for money**. A real bank doesn't take wishes for money, so we don't teach that.

Done: **Coin visuals** (explanations as coins, calendars and slots instead of sentences and bars). Spec and mockup in `docs/superpowers/specs/2026-09-19-coin-visuals-*`. Left in the backlog from it: celebration animations and the standing order loop.

## Training wheels

- ~~**Weekly review (home card).**~~ Done: rolling 7 days, `ledger.week_summary`, hidden when the week is empty.
- **"Can I afford this?" on transfer.** Show what is left after the transfer and warn when it
  uses most of the balance. Extends the existing before/after line.
- **Interest preview on the checking account.** "If you spend nothing for a year: +X €", or a small growth chart.
  Shows compounding and waiting.

## Better understanding

- **Lesson on old statement lines.** Tap a line for a one-sentence explanation ("Interest: the bank
  pays you for waiting"). Celebrations do this now, statements do not.
- **Term deposit countdown on the home tile.** "3 days left 🔒" or 🎁 when ready, so collecting is hard to miss.
- ~~**Parent view of a kid's statement.**~~ Done: `/parent/kids/{uid}/account` (checking only, reuses `statement.html`).
- **Kid-facing stocks.** `market.py` and the tables exist and are tested; only the UI is missing.
  Lesson: prices can go down. Gate on `stocks_enabled`, probably for the 7-8 year olds.

## System

- **Backup/export.** Parent-only "Download backup" (SQLite `.backup`). Everything lives in `kiddybank.db`.
  Note: savings goal photos are stored in the DB, so they are covered too. Does not touch the deployment milestone.
- **Undo for parent bookings.** One reversal `Transaction` instead of a manual counter-booking after a typo.

## Savings goal follow-ups (deliberately left out of v1)

- **Time estimate.** "In about 6 weeks with your pocket money", derived from `RecurringRule` and the checking rate.
- Money earmarking, shared/family goals, image cropping, non-JPEG photos, parent approval of goals.

## Suggested order

Weekly review and the parent statement view first (most visible, existing data only), then the
backup.
