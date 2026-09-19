# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Kinderbank: a self-hosted banking simulation for kids aged 5-8, meant to teach how banking works. FastAPI + SQLModel + SQLite, server-rendered Jinja2 with HTMX and Tailwind, installable as a PWA.

## Commands

```sh
uv sync                                   # install deps (uv.lock is committed)
uv run uvicorn app.main:app --reload      # run; first visit to / redirects to /setup to create the parent
uv run pytest                             # all tests
uv run pytest tests/test_ledger.py::test_name   # single test
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify   # rebuild CSS after touching templates
```

- `bin/tailwindcss` is the Tailwind v4 standalone binary, gitignored (`bin/`). Download it yourself if missing. `app/static/app.css` is its **committed** output, so rebuild and commit it whenever you add utility classes to a template. Custom component classes (`.btn`, `.tile`, `.card`, ...) live in `app/tailwind.css`.
- `uv run ruff check` (pyflakes + bugbear, config in `pyproject.toml`) runs in CI, as does a check that `app/static/app.css` matches a fresh Tailwind build (pinned to v4.3.3, keep it equal to your local `bin/tailwindcss`). No formatter is configured.
- Runtime state that is gitignored: `kiddybank.db` (override with `KIDDYBANK_DB`) and `.session_secret` (override with `KIDDYBANK_SECRET`).

## Architecture

Layering: `main.py` (app, middleware, error handlers) → `routes/{login,banking,savings,parent}.py` (routes, form parsing, view models; shared plumbing in `web.py`: session, dependencies, `render`, parsers) → `ledger.py` (accounts, `post`, interest, allowance) with `festgeld.py`, `goals.py`, `events.py` and `market.py` on top of it (`festgeld`/`goals` import `ledger`, never the reverse) → `models.py` (SQLModel tables + engine). `i18n.py` and `auth.py` are leaf helpers.

**Ledger rules (`ledger.py`, `festgeld.py`, `goals.py`)** — these span several files, so read them before touching money code:
- Money is integer cents; rates are integer basis points (`parse_euro` / `parse_percent` in `web.py` convert user text).
- Ledger functions `flush` but never `commit`. The transaction belongs to the request: `get_session` commits on success and rolls back on exception. Ledger functions validate before they mutate, so a `LedgerError` leaves nothing half-done and routes just re-render with the key; do not `rollback()` (it would also throw away the lazy catch-up that just ran). Keep it that way: check first, then change.
- Errors are `LedgerError("i18n.key")`, never sentences; routes pass the key to the template.
- Every booking goes through `post()`, which settles both accounts (`ensure_up_to_date`) before it moves money; the settling code itself uses the raw `_post()`. Timestamps come from `stamp(today)`, never `datetime.now()`. A `None` side of a `Transaction` is the virtual parent/bank/market. Statement balances are derived backwards from `Account.balance_cents`, not stored per row.
- **Interest and pocket money are lazy.** There is no scheduler. `ensure_up_to_date()` (interest accrual + due `RecurringRule`s) must run before *any* balance change on an account, so interest is computed on the balance that actually held; `post()` guarantees it. The `kid` and `parent` dependencies in `web.py` call `catch_up_user` on every request. `ensure_up_to_date` replays payouts and allowance runs in date order, so results and statement dates do not depend on when anyone opened the app. Interest accrues into `Account.interest_accrued` (remainder carried between payouts, unit 1/(10000·365) cent; payouts round to the nearest cent) and is booked every `Account.payout_days` (7/30/365, `PAYOUT_PERIODS`, chosen per kid) from `interest_paid_on`.
- **Rates are stored as annual basis points, but parents enter and kids see them per period** (`ledger.annual_bp` / `period_bp`, `parse_rate` in `web.py`, `per` template filter). A kid sees every rate in *their own* `payout_days`, so Giro and Festgeld compare like for like. `FestgeldProduct.rate_days` only remembers the unit the parent typed. `MAX_RATE_BP` is 100 % per week.
- Festgeld deposits are separate `Account` rows (`type="festgeld"`) that snapshot name, rate and maturity from a `FestgeldProduct` at opening. They are never auto-paid: the kid collects via `collect_festgeld` ("Abholen") after maturity. Products are plain rows with no active flag or edit form: to change one, delete and recreate it (deposits reference nothing). Collecting is deliberately not gated on the per-kid `festgeld_enabled` switch.
- Sparziele (`Goal`) are progress-only: `goal_progress` / `goal_reached` compare the Giro balance to `target_cents`, nothing is reserved and there is no ledger booking. Finishing a goal ("Ziel geschafft!") only sets `done_at`; a parent books any real withdrawal by hand. Photos are JPEG BLOBs (max `MAX_PHOTO_BYTES`; `list_goals` defers them, templates ask `Goal.has_photo`), resized in the browser and served by `/ziele/{id}/bild` to the owner or a parent only. The reached-goal celebration is dismissed by the same `POST /gesehen` as interest and allowance.
- Einzahlen/Abheben (`/bar/{einzahlen|abheben}`) are the kid-side of real cash changing hands: the kid picks an amount in their own session, then any parent types their PIN on the same screen (checked against every parent hash, no parent login). It books via `manual_booking`, so it shows up as "Eltern haben Geld eingezahlt/abgehoben". Withdrawing shows the interest the kid gives up.
- Forms that move money (transfer, Einzahlen/Abheben, opening a Festgeld) carry a one-time token (`issue_token` / `use_token`, kept in the session): a double tap posts the form twice and only the first counts. `app.js` also sends each form once. Tests go through the confirm step (`confirm()` / `open_deposit()` helpers).
- "Today" comes from the `get_today` dependency, not `date.today()`, so tests can move the clock (`client.clock["today"]`). Use the dependency in new routes.

**Stocks:** `market.py` (seeded random walk, caught up lazily per day) and the `Stock`/`PriceHistory`/`Holding` tables exist and are tested, but no route or template exposes them yet. Only the per-kid `stocks_enabled` flag is wired into the parent UI.

**SQLite setup (`make_engine`)** — WAL, foreign keys on, and every transaction starts with `BEGIN IMMEDIATE` so writers serialize. This matters because GET requests write (lazy catch-up). Tables are created with `create_all`; columns added later are listed in `models.ADDED_COLUMNS` and `_migrate` adds them to older DBs on startup (additive changes only: renames or drops need a manual migration).

**Auth** — profile picker + 4-digit PIN (scrypt), signed session cookie holding `uid` and `seen`; parent sessions expire after `PARENT_IDLE` (15 min) without a request, kids' last the cookie's 30 days. Five wrong PINs in a row lock a user for five minutes (`auth.pin_failed`); this counts login attempts against the profile and cash-desk attempts against the kid asking, so a kid cannot guess a parent PIN at `/bar/*`. Roles are `parent` / `child`: `Depends(kid)` redirects parents to `/eltern` and `Depends(parent)` returns 403 for kids (dependencies redirect by raising `Redirect`; `HTTPException(status, "i18n.key")` renders `error.html`, never raw JSON). Kid routes must scope every account lookup to `user.id` (see `statement`, `_resolve`, `festgeld_collect`).

**Frontend** — no JS build step. Routes use German paths (`/ueberweisen`, `/eltern`, `/konto/{id}`, `/festgeld/.../abholen`, `/ziele` for Sparziele). `app/static/app.js` is the amount stepper and PIN keypad (kids tap buttons instead of typing); HTMX is vendored and used for partials like `_preview.html`. `sw.js` is served from `/sw.js` (root scope): cache-first static, network-first pages, never caches POSTs, and wipes cached pages on logout so a shared tablet can't leak balances. `sw.js` caches `/static/` cache-first, which is safe because templates link assets through `static_url()` (`?v=<mtime>`), so a changed `app.js`/`app.css` is a new URL; only bump the `ASSETS`/`PAGES` names in `sw.js` when its own caching logic changes (its `activate` handler deletes older cache names).

**Tests** — `tests/test_ledger.py` calls ledger/market functions directly against a temp SQLite DB. `tests/test_app.py` drives real routes with `TestClient`; its `client` fixture points `web.DB_URL` at a temp file, clears the `web._engine` cache and overrides `web.get_today`; the `family` fixture creates a parent and a kid through the routes.

## Design docs and backlog

- `docs/ideas.md`: backlog of feature ideas that are not designed yet. Start here when asked what to build next. When an idea gets a spec, mark it done or in progress there.
- `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`: approved design specs, one per feature (currently `2026-09-19-sparziele-design.md`). Read the matching spec before implementing or changing that feature; it records the owner's decisions and what was deliberately left out. Implementation plans, if written, go next to them.

## Product constraints (decided by the owner, check new features against these)

- All UI text goes through `t()` in `app/i18n.py` (German today, structured for more locales). No hardcoded user-facing strings in routes or templates.
- **No sound, ever.** Feedback is visual only.
- Every banking event or screen explains its concept in one kid-sized sentence.
- Exactly one Giro per kid plus any number of Festgeld deposits. **No Spar/Tagesgeld account.**
- All interest rates are parent-configurable. Don't hardcode new rates; `DEFAULT_*` constants in `ledger.py` are only seeds.
