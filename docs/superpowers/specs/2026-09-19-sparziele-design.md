# Sparziele (savings goals)

Kids aged 5-8 can pick something to save for, ideally with a photo, and watch a progress bar fill.
Teaches saving toward a purpose. A goal may be anything (a toy, a trip, a gift, a donation), so
the wording never assumes a purchase.

## Decisions (owner-approved)

- **Progress only, no earmarking.** A goal is a label, a target and an optional photo. Progress is
  `giro.balance_cents / target_cents`, capped at 100 %. No money is locked, no ledger rule changes,
  and it is not a Spar account (see CLAUDE.md product constraints).
- **Kid creates, parent can see and delete.** Parents get a list per kid in `/eltern` with a delete button on every goal, finished ones included (the kid cannot delete those): `POST /eltern/ziele/{id}/loeschen`.
- **Reached = celebrate, then the kid taps "Ziel geschafft!"** which archives the goal. The app never
  books money for it; the parent books any withdrawal via the existing manual booking.
- **Photo is the primary visual**, because 5-year-olds cannot read or type. Emoji is the fallback.

## Data

New table (created by `create_all`, no migration needed; no existing table changes):

```
Goal(id, user_id -> user.id (indexed), name: str = "", emoji: str = "🎯",
     target_cents: int, photo: bytes | None, created_at: datetime,
     reached_seen_at: datetime | None, done_at: datetime | None)
```

- Derived, not stored: `reached = done_at is None and giro.balance_cents >= target_cents`.
- A goal needs a `photo` or a non-empty `name`; otherwise `LedgerError("err.goal_empty")`.
- `target_cents > 0`, else `err.amount`.
- At most 3 active goals (`done_at is None`) per kid, else `err.goal_limit`.
- If the goal is already affordable at creation, set `reached_seen_at = now` so there is no fake celebration.
- Each bar compares against the whole Giro balance; two goals can both show full. Accepted.

Goal rules (validation, limit, reached check) live in `ledger.py` next to the other money-adjacent
logic. They `flush` but never `commit`, and raise `LedgerError("i18n.key")`.

## Routes (kid, `Depends(kid)`, every lookup scoped to `user.id`)

| Route | Purpose |
|---|---|
| `GET /ziele` | Active goals with bars and "Noch X bis dahin", archived goals with a "Geschafft" badge, create form |
| `POST /ziele` | Create (multipart: `name`, `emoji`, `cents`, optional `photo`). On `LedgerError`: `s.rollback()`, re-render with the key |
| `POST /ziele/{id}/loeschen` | Delete an unfinished goal |
| `POST /ziele/{id}/geschafft` | Only if reached; sets `done_at` |
| `POST /gesehen` (existing) | "Toll!" now also sets `reached_seen_at` on reached goals, so one celebration screen has one dismiss |
| `GET /ziele/{id}/bild` | Photo bytes. Owner or a parent, else 404 |

Home (`home.html`): a card with the nearest active goal's picture and bar (a plain "Sparziele" tile with the 🎯 emoji when the kid has no active goal); reached-and-unseen goals show the existing pink confetti screen. `_celebration.html` has a `goal` event type next to `zins` / `dauerauftrag`, showing the goal picture, "Du hast dein Ziel erreicht!" (`cel.goal`), and the goal name on its own line when there is one.

Parents: `/eltern` lists each kid's goals read-only (photo, name, target, progress).

## Photo handling

- `<input type="file" accept="image/*">` behind a large 📷 button; the OS offers camera or gallery.
- `app.js` (about 20 lines): draw onto a canvas at <= 512 px on the long side, `toBlob('image/jpeg', 0.8)`,
  put the result in the form's file field. Raw phone photos are 3 MB+, so the client resize is required.
- Server: reject over 300 KB (`err.photo_size`), reject anything not starting with `FF D8 FF`
  (`err.photo_type`). Ignore filename and client content type. Always serve as `image/jpeg` with
  `X-Content-Type-Options: nosniff` and `Cache-Control: private, no-cache`.
- Stored as a BLOB, so `kiddybank.db` stays the single file to back up.
- photo GETs are never cached by `sw.js` (it only caches navigations and `/static/`), so a shared tablet cannot leak them; separately, Task 5 bumped the `ASSETS` cache to `kb-assets-v2` and made `activate` delete older caches so installed PWAs pick up the new `app.js` and `app.css`.
- `# ponytail:` comment at the validation: JPEG only, no Pillow. Add Pillow if other formats or
  server-side resizing are ever needed.
- Optional delight: the photo is grayscale at 0 % and full colour at 100 %, via inline CSS
  `filter: grayscale(...)` from the progress value.

## Text (all through `t()` in `i18n.py`, German only, no sound)

- Lesson on `/ziele`: "Ein Sparziel ist etwas, worauf du sparst. Der Balken zeigt, wie nah du schon dran bist."
- Button: "🎉 Ziel geschafft!"; badge: "✅ Geschafft".
- Hint after tapping: "Brauchst du dafür Geld von deinem Konto? Dann sag Mama oder Papa Bescheid."
- Errors: `err.goal_empty`, `err.goal_limit`, `err.photo_size`, `err.photo_type`.

## Tests (`tests/test_app.py`, real routes)

- Create with name only, with photo only, and with neither (rejected); the 4th active goal is rejected.
- Oversized and non-JPEG uploads are rejected; a valid JPEG round-trips through `/bild`.
- A second kid gets 404 on the first kid's goal actions and photo; a parent can fetch the photo.
- Celebration appears once when the balance crosses the target, and not again after `/gesehen`.
- `/geschafft` on a goal that is not reached fails; on a reached goal it archives it.
- Rebuild `app/static/app.css` after adding utility classes.

## Out of scope

Time estimate ("in ca. 6 Wochen bei deinem Taschengeld", would tie into `RecurringRule`), money
earmarking, shared or family goals, image cropping or editing, multiple photos, non-JPEG formats,
parent approval of goals.
