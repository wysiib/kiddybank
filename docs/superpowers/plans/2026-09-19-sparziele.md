# Sparziele Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kids can create savings goals (photo or emoji, name optional, target price), see a progress bar against their Giro balance, get a one-time celebration when it is reached, and archive it with "Ziel geschafft!". Parents see the goals read-only.

**Architecture:** One new `Goal` table. Goal rules live in `ledger.py` (flush, never commit, `LedgerError("key")`). Progress and "reached" are derived from `Account.balance_cents`, nothing is stored or reserved. Routes under `/ziele` in `main.py`, a shared `_goal.html` macro file for photo and bar. Photos are resized to JPEG in the browser (`app.js`), validated on the server (size and magic bytes), stored as a BLOB.

**Tech Stack:** FastAPI, SQLModel/SQLite, Jinja2, HTMX (untouched), Tailwind v4 standalone binary, vanilla JS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-19-sparziele-design.md`

## Global Constraints

- All user-facing text goes through `t()` in `app/i18n.py` (German). No hardcoded strings in routes or templates. No sound.
- Money is integer cents. Ledger functions `flush` but never `commit`. Errors are `LedgerError("i18n.key")`, never sentences. Routes that catch a `LedgerError` call `s.rollback()` before re-rendering.
- Kid routes use `Depends(kid)` and scope every goal lookup to `user.id`.
- Wording: the finished state is "Geschafft" / "Ziel geschafft!". Never "gekauft" (a goal need not be a purchase).
- At most 3 active goals per kid. A goal needs a photo or a non-empty name. Target must be > 0.
- Photos: JPEG only (magic bytes `FF D8 FF`), at most 300 000 bytes on the server (`MAX_PHOTO_BYTES`), resized to at most 512 px in the browser, no Pillow, no new dependency.
- Photos are served only by `GET /ziele/{id}/bild` to the owner or a parent, as `image/jpeg` with `X-Content-Type-Options: nosniff` and `Cache-Control: private, no-cache`.
- No account or ledger rule changes: no earmarking, goals are not a Spar account.
- After adding utility classes to a template, rebuild and commit `app/static/app.css`: `bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify`.
- Commit messages end with the two trailer lines shown in the commit steps.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `app/models.py` | modify | `Goal` table |
| `app/ledger.py` | modify | goal rules: create, progress, reached, finish, unseen; `mark_seen` also dismisses goal celebrations |
| `app/main.py` | modify | `/ziele` routes, `_goal_cards`, home and parent context |
| `app/i18n.py` | modify | goal strings |
| `app/templates/_goal.html` | create | `visual` (photo or emoji) and `bar` macros shared by all goal screens |
| `app/templates/goals.html` | create | kid goal list and create form |
| `app/templates/home.html` | modify | nearest-goal card or Sparziele tile |
| `app/templates/_celebration.html` | modify | `goal` event |
| `app/templates/parent.html` | modify | read-only goal list per kid |
| `app/static/app.js` | modify | photo resize and preview |
| `app/static/sw.js` | modify | purge old caches, bump asset cache version |
| `tests/test_ledger.py`, `tests/test_app.py` | modify | tests |

---

### Task 1: Goal model and ledger rules

**Files:**
- Modify: `app/models.py` (add `Goal` after `Transaction`)
- Modify: `app/ledger.py` (import, `mark_seen`, new goals section at the end)
- Test: `tests/test_ledger.py` (append)

**Interfaces:**
- Produces (used by Tasks 2 to 4):
  - `Goal` fields: `id, user_id, name: str, emoji: str, target_cents: int, photo: bytes | None, created_at: datetime, reached_seen_at: datetime | None, done_at: datetime | None`
  - `ledger.MAX_ACTIVE_GOALS = 3`, `ledger.MAX_PHOTO_BYTES = 300_000`
  - `ledger.list_goals(s, user_id) -> list[Goal]` (ordered by id)
  - `ledger.goal_progress(goal, giro) -> int` (0..100; 100 once done)
  - `ledger.goal_reached(goal, giro) -> bool` (not done and balance >= target)
  - `ledger.create_goal(s, user_id, name, emoji, target_cents, photo) -> Goal`
  - `ledger.finish_goal(goal, giro) -> None`
  - `ledger.unseen_reached_goals(s, user_id) -> list[Goal]`
  - `ledger.mark_seen(s, user_id)` now also sets `reached_seen_at` on unseen reached goals
  - Error keys: `err.amount`, `err.goal_empty`, `err.goal_limit`, `err.photo_size`, `err.photo_type`, `err.goal_not_reached`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py`:

```python
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def make_goal(s, user, **kw):
    args = {"name": "Lego", "emoji": "🧸", "target_cents": 500, "photo": None, **kw}
    return ledger.create_goal(s, user.id, **args)


def test_goal_validation_and_photo_roundtrip(s, kids):
    mia, _ = kids
    with pytest.raises(LedgerError, match="err.amount"):
        make_goal(s, mia, target_cents=0)
    with pytest.raises(LedgerError, match="err.goal_empty"):
        make_goal(s, mia, name="  ")
    with pytest.raises(LedgerError, match="err.photo_type"):
        make_goal(s, mia, photo=b"GIF89a")
    with pytest.raises(LedgerError, match="err.photo_size"):
        make_goal(s, mia, photo=JPEG + b"x" * ledger.MAX_PHOTO_BYTES)

    g = make_goal(s, mia, name="", photo=JPEG)  # a photo alone is enough
    s.expire_all()  # force a real read back from SQLite
    assert s.get(Goal, g.id).photo == JPEG


def test_goal_limit_counts_only_active_goals(s, kids):
    mia, tom = kids
    goals = [make_goal(s, mia) for _ in range(ledger.MAX_ACTIVE_GOALS)]
    with pytest.raises(LedgerError, match="err.goal_limit"):
        make_goal(s, mia)
    make_goal(s, tom)  # limit is per kid

    fund(s, giro(s, mia), 500)
    ledger.finish_goal(goals[0], giro(s, mia))
    make_goal(s, mia)  # a finished goal frees a slot


def test_goal_progress_reached_and_finish(s, kids):
    mia, _ = kids
    g = make_goal(s, mia)
    assert ledger.goal_progress(g, giro(s, mia)) == 0

    fund(s, giro(s, mia), 250)
    assert ledger.goal_progress(g, giro(s, mia)) == 50
    assert not ledger.goal_reached(g, giro(s, mia))
    with pytest.raises(LedgerError, match="err.goal_not_reached"):
        ledger.finish_goal(g, giro(s, mia))

    fund(s, giro(s, mia), 300)  # 550 of 500
    assert ledger.goal_progress(g, giro(s, mia)) == 100  # capped
    assert ledger.goal_reached(g, giro(s, mia))
    assert ledger.unseen_reached_goals(s, mia.id) == [g]

    ledger.mark_seen(s, mia.id)
    assert ledger.unseen_reached_goals(s, mia.id) == []  # celebrated once

    ledger.finish_goal(g, giro(s, mia))
    assert g.done_at and not ledger.goal_reached(g, giro(s, mia))
    assert ledger.goal_progress(g, giro(s, mia)) == 100


def test_goal_already_affordable_has_no_celebration(s, kids):
    mia, _ = kids
    fund(s, giro(s, mia), 1000)
    g = make_goal(s, mia)
    assert ledger.goal_reached(g, giro(s, mia))
    assert ledger.unseen_reached_goals(s, mia.id) == []
```

Also add `Goal` to the import line at the top of `tests/test_ledger.py`:

```python
from app.models import FestgeldProduct, Goal, PriceHistory, RecurringRule, Transaction, make_engine
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ledger.py -k goal -v`
Expected: FAIL/ERROR at import (`cannot import name 'Goal'`).

- [ ] **Step 3: Add the model**

In `app/models.py`, after the `Transaction` class:

```python
class Goal(SQLModel, table=True):
    """Savings goal. Progress is derived from the Giro balance; nothing is reserved."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = ""
    emoji: str = "🎯"
    target_cents: int
    photo: bytes | None = None  # JPEG, shrunk in the browser; kept in the DB so it stays the one file to back up
    created_at: datetime
    reached_seen_at: datetime | None = None  # celebration dismissed
    done_at: datetime | None = None  # kid tapped "Ziel geschafft!"
```

- [ ] **Step 4: Add the ledger rules**

In `app/ledger.py` change the models import to:

```python
from .models import Account, FestgeldProduct, Goal, RecurringRule, Transaction, User
```

Replace `mark_seen` with:

```python
def mark_seen(s: Session, user_id: int) -> None:
    now = datetime.now()
    for t in unseen_events(s, user_id):
        t.seen_at = now
    for g in unseen_reached_goals(s, user_id):
        g.reached_seen_at = now
```

Append at the end of `app/ledger.py`:

```python
# --- goals -------------------------------------------------------------------------------------

MAX_ACTIVE_GOALS = 3
MAX_PHOTO_BYTES = 300_000  # the browser shrinks photos to ~50 KB, this only stops abuse


def list_goals(s: Session, user_id: int) -> list[Goal]:
    # ponytail: photos load with the row; defer() them if goal counts ever grow
    return list(s.exec(select(Goal).where(Goal.user_id == user_id).order_by(Goal.id)).all())


def goal_progress(goal: Goal, giro: Account) -> int:
    """Whole percent (0-100) of the target the Giro balance covers."""
    if goal.done_at:
        return 100
    return max(0, min(100, giro.balance_cents * 100 // goal.target_cents))


def goal_reached(goal: Goal, giro: Account) -> bool:
    return goal.done_at is None and giro.balance_cents >= goal.target_cents


def create_goal(s: Session, user_id: int, name: str, emoji: str, target_cents: int, photo: bytes | None) -> Goal:
    name = name.strip()[:40]
    if target_cents <= 0:
        raise LedgerError("err.amount")
    if photo is not None:
        if len(photo) > MAX_PHOTO_BYTES:
            raise LedgerError("err.photo_size")
        # ponytail: JPEG only (the browser re-encodes everything to JPEG); add Pillow if other formats are ever needed
        if not photo.startswith(b"\xff\xd8\xff"):
            raise LedgerError("err.photo_type")
    if not name and photo is None:
        raise LedgerError("err.goal_empty")
    if sum(1 for g in list_goals(s, user_id) if g.done_at is None) >= MAX_ACTIVE_GOALS:
        raise LedgerError("err.goal_limit")
    now = datetime.now()
    goal = Goal(user_id=user_id, name=name, emoji=emoji, target_cents=target_cents, photo=photo, created_at=now)
    if goal_reached(goal, get_account(s, user_id, "giro")):
        goal.reached_seen_at = now  # already affordable at creation: no fake celebration
    s.add(goal)
    s.flush()
    return goal


def finish_goal(goal: Goal, giro: Account) -> None:
    if not goal_reached(goal, giro):
        raise LedgerError("err.goal_not_reached")
    goal.done_at = datetime.now()


def unseen_reached_goals(s: Session, user_id: int) -> list[Goal]:
    giro = get_account(s, user_id, "giro")
    return [g for g in list_goals(s, user_id) if g.reached_seen_at is None and goal_reached(g, giro)]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: all pass, including the four new goal tests. If `test_goal_validation_and_photo_roundtrip` fails on the photo type, check that SQLModel maps `bytes | None` to a binary column; if not, use `photo: bytes | None = Field(default=None, sa_column=Column(LargeBinary))` with `from sqlalchemy import Column, LargeBinary`.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/ledger.py tests/test_ledger.py
git commit -m "$(cat <<'EOF'
Add Goal model and savings-goal ledger rules

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UFuTjGtkgsoXHa1DeZkUAd
EOF
)"
```

---

### Task 2: Kid goal screens (list, create, delete, finish, photo)

**Files:**
- Modify: `app/main.py` (imports, `GOAL_EMOJIS`, goals section after the Festgeld section)
- Modify: `app/i18n.py`
- Create: `app/templates/_goal.html`, `app/templates/goals.html`
- Test: `tests/test_app.py` (append)

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces (used by Tasks 3 and 4):
  - `main._goal_cards(goals: list[Goal], giro: Account) -> list[dict]`, each `{"goal": Goal, "pct": int, "reached": bool}`
  - `main.GOAL_EMOJIS: list[str]`
  - Template macros `visual(g, pct, cls="h-24 w-24")` and `bar(pct)` in `_goal.html`
  - Routes: `GET/POST /ziele`, `POST /ziele/{id}/loeschen`, `POST /ziele/{id}/geschafft`, `GET /ziele/{id}/bild`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def add_goal(c, **kw):
    return c.post("/ziele", data={"name": "Lego", "emoji": "🧸", "cents": 2000, **kw})


def test_goal_create_photo_and_progress(family):
    login(family, 2, "1111")
    assert add_goal(family, name="").status_code == 400  # neither name nor photo
    r = family.post("/ziele", data={"name": "", "cents": 2000}, files={"photo": ("g.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 303
    page = family.get("/ziele").text
    assert "/ziele/1/bild" in page and "Noch 10,00 € bis dahin" in page  # 10 EUR of 20 EUR
    img = family.get("/ziele/1/bild")
    assert img.content == JPEG and img.headers["content-type"] == "image/jpeg"
    assert img.headers["x-content-type-options"] == "nosniff"

    assert add_goal(family).status_code == 303  # name only works too
    assert "Lego" in family.get("/ziele").text


def test_goal_rejects_bad_input(family):
    login(family, 2, "1111")
    r = family.post("/ziele", data={"name": "x", "cents": 500}, files={"photo": ("g.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 400 and "geht es leider nicht" in r.text
    big = JPEG + b"x" * ledger.MAX_PHOTO_BYTES
    r = family.post("/ziele", data={"name": "x", "cents": 500}, files={"photo": ("g.jpg", big, "image/jpeg")})
    assert r.status_code == 400 and "zu groß" in r.text
    assert add_goal(family, cents=0).status_code == 400

    for _ in range(3):
        assert add_goal(family).status_code == 303
    r = add_goal(family)
    assert r.status_code == 400 and "schon 3" in r.text


def test_goal_scoping_between_kids_and_parent(family):
    login(family, 2, "1111")
    family.post("/ziele", data={"name": "Lego", "cents": 2000}, files={"photo": ("g.jpg", JPEG, "image/jpeg")})

    login(family, 1, "1234")
    family.post("/eltern/kinder", data={"name": "Tom", "pin": "2222"})
    assert family.get("/ziele/1/bild").status_code == 200  # a parent may see it

    login(family, 3, "2222")
    assert family.get("/ziele/1/bild").status_code == 404
    assert family.post("/ziele/1/loeschen").status_code == 404
    assert family.post("/ziele/1/geschafft").status_code == 404
    assert "Lego" not in family.get("/ziele").text


def test_goal_finish_and_delete(family):
    login(family, 2, "1111")
    add_goal(family, cents=1500)  # goal 1: 10 EUR of 15 EUR
    add_goal(family, name="Ball", cents=500)  # goal 2: already affordable

    r = family.post("/ziele/1/geschafft")
    assert r.status_code == 400 and "nicht genug Geld" in r.text  # not reached yet

    r = family.post("/ziele/2/geschafft")
    assert r.status_code == 200 and "Mama oder Papa" in r.text
    page = family.get("/ziele").text
    assert "✅ Geschafft" in page and "Ball" in page
    assert family.post("/ziele/2/loeschen").status_code == 404  # finished goals stay as a record

    assert family.post("/ziele/1/loeschen").status_code == 303
    assert "Lego" not in family.get("/ziele").text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -k goal -v`
Expected: FAIL (404 on `/ziele`).

- [ ] **Step 3: Add strings**

In `app/i18n.py`, add after the `"fg.none"` line:

```python
        # goals
        "goal.title": "Sparziele",
        "goal.lesson": "Ein Sparziel ist etwas, worauf du sparst. Der Balken zeigt, wie nah du schon dran bist.",
        "goal.none": "Du hast noch kein Sparziel.",
        "goal.left": "Noch {amount} bis dahin",
        "goal.reached": "Du hast genug gespart!",
        "goal.finish": "Ziel geschafft!",
        "goal.done": "Geschafft",
        "goal.done.msg": "Ziel geschafft!",
        "goal.done.hint": "Brauchst du dafür Geld von deinem Konto? Dann sag Mama oder Papa Bescheid.",
        "goal.delete": "Löschen",
        "goal.new": "Neues Sparziel",
        "goal.photo": "Foto machen",
        "goal.name": "Name (oder nur ein Foto)",
        "goal.price": "Wie viel Geld brauchst du?",
        "goal.create": "Sparziel anlegen",
```

and after the `"err.name"` line:

```python
        "err.goal_empty": "Gib dem Sparziel einen Namen oder mach ein Foto.",
        "err.goal_limit": "Du hast schon 3 Sparziele. Schaffe erst eins davon!",
        "err.goal_not_reached": "Dafür ist noch nicht genug Geld da.",
        "err.photo_size": "Das Foto ist zu groß.",
        "err.photo_type": "Mit diesem Foto geht es leider nicht.",
```

- [ ] **Step 4: Create the shared macros**

Create `app/templates/_goal.html`:

```jinja
{# Goal picture (photo fades from gray to color as the bar fills, else the emoji) and progress bar. #}
{% macro visual(g, pct, cls="h-24 w-24") %}
{% if g.photo %}<img src="/ziele/{{ g.id }}/bild" alt="{{ g.name }}" class="{{ cls }} mx-auto rounded-2xl object-cover" style="filter: grayscale({{ 100 - pct }}%)">
{% else %}<span class="text-6xl">{{ g.emoji }}</span>{% endif %}
{% endmacro %}

{% macro bar(pct) %}
<div class="h-5 w-full overflow-hidden rounded-full bg-slate-200"><div class="h-full rounded-full bg-pink-500" style="width: {{ pct }}%"></div></div>
{% endmacro %}
```

- [ ] **Step 5: Create the goals page**

Create `app/templates/goals.html`:

```jinja
{% extends "base.html" %}
{% from "_stepper.html" import stepper %}
{% from "_goal.html" import visual, bar %}
{% block content %}
<h1 class="text-3xl font-extrabold">🎯 {{ t('goal.title') }}</h1>
<p class="lesson">{{ t('goal.lesson') }}</p>
{% if error %}<p class="error">{{ t(error) }}</p>{% endif %}

{% for c in active %}
<div class="card space-y-3 text-center">
  {{ visual(c.goal, c.pct, 'h-48 w-48') }}
  {% if c.goal.name %}<p class="text-2xl font-extrabold">{{ c.goal.name }}</p>{% endif %}
  {{ bar(c.pct) }}
  <p class="text-lg text-slate-500">{{ giro.balance_cents|money }} / {{ c.goal.target_cents|money }}</p>
  {% if c.reached %}
  <p class="text-2xl font-extrabold">{{ t('goal.reached') }}</p>
  <form method="post" action="/ziele/{{ c.goal.id }}/geschafft"><button class="btn w-full">🎉 {{ t('goal.finish') }}</button></form>
  {% else %}
  <p class="text-2xl font-bold">{{ t('goal.left', amount=(c.goal.target_cents - giro.balance_cents)|money) }}</p>
  {% endif %}
  <form method="post" action="/ziele/{{ c.goal.id }}/loeschen"><button class="btn btn-alt">🗑️ {{ t('goal.delete') }}</button></form>
</div>
{% else %}
<p class="card text-center">{{ t('goal.none') }}</p>
{% endfor %}

{% if can_add %}
<h2 class="text-2xl font-extrabold">{{ t('goal.new') }}</h2>
<form method="post" action="/ziele" enctype="multipart/form-data" class="space-y-6">
  <section class="space-y-3 text-center">
    <label class="btn w-full cursor-pointer">📷 {{ t('goal.photo') }}
      <input type="file" name="photo" accept="image/*" class="sr-only" data-photo>
    </label>
    <img data-photo-preview class="mx-auto hidden h-48 w-48 rounded-2xl object-cover" alt="">
  </section>
  <section class="space-y-3">
    <label class="block font-bold">{{ t('goal.name') }}<input class="input" name="name" maxlength="40"></label>
    <div class="flex flex-wrap gap-2">
      {% for e in emojis %}
      <label class="choice rounded-2xl bg-white p-2 text-4xl shadow"><input type="radio" name="emoji" value="{{ e }}" class="sr-only" {{ 'checked' if loop.first }}>{{ e }}</label>
      {% endfor %}
    </div>
  </section>
  <section class="space-y-3">
    <h3 class="font-bold">{{ t('goal.price') }}</h3>
    {{ stepper() }}
  </section>
  <button class="btn w-full">🎯 {{ t('goal.create') }}</button>
</form>
{% endif %}

{% if done %}
<h2 class="text-2xl font-extrabold">✅ {{ t('goal.done') }}</h2>
{% for c in done %}
<div class="card flex items-center gap-4">
  {{ visual(c.goal, 100, 'h-16 w-16') }}
  <div class="flex-1"><div class="font-bold">{{ c.goal.name }}</div><div class="text-base text-slate-500">{{ c.goal.target_cents|money }}</div></div>
</div>
{% endfor %}
{% endif %}
<a href="/home" class="btn btn-alt">↩️ {{ t('app.back') }}</a>
{% endblock %}
```

- [ ] **Step 6: Add the routes**

In `app/main.py`:

Change the imports:

```python
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
```

```python
from .models import Account, FestgeldProduct, Goal, RecurringRule, User, make_engine
```

Below `AVATARS = [...]` add:

```python
GOAL_EMOJIS = ["🎯", "🧸", "🚲", "⚽", "🎮", "📚", "🎁", "✈️", "🐶", "🍦"]
```

Insert before the `# --- parents ---` section:

```python
# --- kid: goals --------------------------------------------------------------------------------

def _goal_cards(goals: list[Goal], giro: Account) -> list[dict]:
    return [{"goal": g, "pct": ledger.goal_progress(g, giro), "reached": ledger.goal_reached(g, giro)} for g in goals]


def _goals_page(request: Request, s: Session, user: User, error: str | None = None):
    giro = ledger.get_account(s, user.id, "giro")
    cards = _goal_cards(ledger.list_goals(s, user.id), giro)
    active = [c for c in cards if not c["goal"].done_at]
    done = [c for c in reversed(cards) if c["goal"].done_at]
    return render(request, "goals.html", 200 if not error else 400, user=user, giro=giro, active=active, done=done,
                  can_add=len(active) < ledger.MAX_ACTIVE_GOALS, emojis=GOAL_EMOJIS, error=error)


def _own_goal(s: Session, user: User, goal_id: int) -> Goal:
    g = s.get(Goal, goal_id)
    if not g or g.user_id != user.id:
        raise HTTPException(404)
    return g


@app.get("/ziele")
def goals_page(request: Request, user: User = Depends(kid), s: Session = Depends(get_session)):
    return _goals_page(request, s, user)


@app.post("/ziele")
def goal_create(request: Request, name: str = Form(""), emoji: str = Form(""), cents: int = Form(0),
                photo: UploadFile | None = File(None), user: User = Depends(kid), s: Session = Depends(get_session)):
    data = photo.file.read(ledger.MAX_PHOTO_BYTES + 1) if photo else b""  # bounded read, size is checked in the ledger
    try:
        ledger.create_goal(s, user.id, name, emoji if emoji in GOAL_EMOJIS else GOAL_EMOJIS[0], cents, data or None)
    except LedgerError as e:
        s.rollback()
        return _goals_page(request, s, user, e.args[0])
    return redirect("/ziele")


@app.post("/ziele/{goal_id}/loeschen")
def goal_delete(goal_id: int, user: User = Depends(kid), s: Session = Depends(get_session)):
    g = _own_goal(s, user, goal_id)
    if g.done_at:
        raise HTTPException(404)  # finished goals stay as a record
    s.delete(g)
    return redirect("/ziele")


@app.post("/ziele/{goal_id}/geschafft")
def goal_finish(request: Request, goal_id: int, user: User = Depends(kid), s: Session = Depends(get_session)):
    g = _own_goal(s, user, goal_id)
    try:
        ledger.finish_goal(g, ledger.get_account(s, user.id, "giro"))
    except LedgerError as e:
        s.rollback()
        return _goals_page(request, s, user, e.args[0])
    return render(request, "done.html", user=user, emoji="✅", msg=t("goal.done.msg"), lesson=t("goal.done.hint"))


@app.get("/ziele/{goal_id}/bild")
def goal_photo(goal_id: int, user: User = Depends(current_user), s: Session = Depends(get_session)):
    g = s.get(Goal, goal_id)
    if not g or not g.photo or (user.role != "parent" and g.user_id != user.id):
        raise HTTPException(404)
    return Response(g.photo, media_type="image/jpeg",
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"})
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all pass. If `add_goal` (urlencoded body, no file) fails with 422, the `File(None)` parameter is forcing multipart. Keep the route as is and make the `add_goal` helper send multipart by adding `files={"_": ("", b"")}`. Real browsers send an empty `photo` part when nothing was picked, so also check once that `files={"photo": ("", b"", "application/octet-stream")}` creates a goal without a photo.

- [ ] **Step 8: Rebuild CSS and commit**

```bash
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add app/main.py app/i18n.py app/templates/_goal.html app/templates/goals.html app/static/app.css tests/test_app.py
git commit -m "$(cat <<'EOF'
Add Sparziele screens: list, create with photo, finish, delete

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UFuTjGtkgsoXHa1DeZkUAd
EOF
)"
```

---

### Task 3: Home card and goal celebration

**Files:**
- Modify: `app/main.py` (`_events`, `home`)
- Modify: `app/templates/home.html`, `app/templates/_celebration.html`
- Modify: `app/i18n.py`
- Modify: `docs/superpowers/specs/2026-09-19-sparziele-design.md` (routes table)
- Test: `tests/test_app.py` (append)

**Interfaces:**
- Consumes: `_goal_cards`, macros `visual`/`bar`, `ledger.unseen_reached_goals`, `ledger.mark_seen` (Tasks 1 and 2).
- Produces: `_events` also returns `{"type": "goal", "goal": Goal}` entries. The existing `POST /gesehen` ("Toll!") dismisses them together with interest and allowance, so there is one celebration screen and one dismiss (this replaces the spec's per-goal `/ziele/{id}/gesehen`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_goal_home_card_and_celebration_once(family):
    login(family, 2, "1111")
    assert "🎯" in family.get("/home").text and "/ziele" in family.get("/home").text  # tile before any goal
    add_goal(family, cents=1500)  # 10 EUR of 15 EUR
    home = family.get("/home").text
    assert "Lego" in home and "dein Ziel erreicht" not in home

    login(family, 1, "1234")
    family.post("/eltern/buchen", data={"account_id": account_id(2, "giro"), "amount": "5,00"})
    login(family, 2, "1111")
    assert "dein Ziel erreicht" in family.get("/home").text
    assert "dein Ziel erreicht" in family.get("/home").text  # looking is not enough
    family.post("/gesehen")
    assert "dein Ziel erreicht" not in family.get("/home").text
    assert "Ziel geschafft" in family.get("/ziele").text  # still reachable, button waits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_goal_home_card_and_celebration_once -v`
Expected: FAIL (no `/ziele` link or no celebration on home).

- [ ] **Step 3: Add strings**

In `app/i18n.py`, after the `"cel.dauerauftrag.why"` line:

```python
        "cel.goal": "Du hast dein Ziel erreicht!",
        "cel.goal.why": "Du hast so lange gespart, bis genug Geld da war. Das nennt man ein Sparziel.",
```

- [ ] **Step 4: Wire the routes**

In `app/main.py`, replace `_events` with:

```python
def _events(s: Session, user: User) -> list[dict]:
    """Unseen interest / allowance (summed per kind) and reached goals, for the celebration screen."""
    totals: dict[str, int] = {}
    for tx in ledger.unseen_events(s, user.id):
        totals[tx.type] = totals.get(tx.type, 0) + tx.amount_cents
    return [{"type": k, "cents": v} for k, v in totals.items()] + [
        {"type": "goal", "goal": g} for g in ledger.unseen_reached_goals(s, user.id)]
```

Replace the `home` route with:

```python
@app.get("/home")
def home(request: Request, user: User = Depends(kid), s: Session = Depends(get_session)):
    giro = ledger.get_account(s, user.id, "giro")
    deposits = [a for a in own_accounts(s, user, "festgeld") if not a.collected_at]
    cards = _goal_cards([g for g in ledger.list_goals(s, user.id) if not g.done_at], giro)
    return render(request, "home.html", user=user, giro=giro, deposits=deposits, top=max(cards, key=lambda c: c["pct"], default=None),
                  events=_events(s, user), rate=_rate, festgeld_visible=user.festgeld_enabled or bool(deposits))
```

(`_goal_cards` is defined further down in the file; that is fine because it is only called at request time.)

- [ ] **Step 5: Update the templates**

In `app/templates/home.html`, add after the first line (`{% extends "base.html" %}`):

```jinja
{% from "_goal.html" import visual, bar %}
```

Insert between the closing `</a>` of the Giro card and `<div class="grid grid-cols-2 gap-4">`:

```jinja
{% if top %}
<a href="/ziele" class="card flex items-center gap-4">
  {{ visual(top.goal, top.pct, 'h-20 w-20') }}
  <div class="flex-1 space-y-2">
    <div class="font-bold">🎯 {{ top.goal.name or t('goal.title') }}</div>
    {{ bar(top.pct) }}
  </div>
</a>
{% endif %}
```

Inside the grid, after the transfer tile, add:

```jinja
  {% if not top %}<a href="/ziele" class="tile"><span class="text-6xl">🎯</span>{{ t('goal.title') }}</a>{% endif %}
```

In `app/templates/_celebration.html`, add `{% from "_goal.html" import visual %}` on line 2 (below the comment line) and replace the `{% for e in events %}...{% endfor %}` block with:

```jinja
    {% for e in events %}
    {% if e.type == 'goal' %}
    <div class="pop card w-full space-y-3">
      {{ visual(e.goal, 100, 'h-40 w-40') }}
      <p class="text-3xl font-extrabold">{{ t('cel.goal') }}</p>
      {% if e.goal.name %}<p class="text-2xl font-bold">{{ e.goal.name }}</p>{% endif %}
      <p class="lesson">{{ t('cel.goal.why') }}</p>
    </div>
    {% else %}
    <div class="pop card w-full space-y-3">
      <div class="text-7xl">{{ '✨' if e.type == 'zins' else '🎉' }}</div>
      <p class="text-3xl font-extrabold">{{ t('cel.' ~ e.type, amount=e.cents|money) }}</p>
      <p class="lesson">{{ t('cel.' ~ e.type ~ '.why') }}</p>
    </div>
    {% endif %}
    {% endfor %}
```

- [ ] **Step 6: Correct the spec**

In `docs/superpowers/specs/2026-09-19-sparziele-design.md`, replace the routes table row

`| \`POST /ziele/{id}/gesehen\` | Sets \`reached_seen_at\` (dismisses the celebration) |`

with

`| \`POST /gesehen\` (existing) | "Toll!" now also sets \`reached_seen_at\` on reached goals, so one celebration screen has one dismiss |`

- [ ] **Step 7: Run all tests**

Run: `uv run pytest -v`
Expected: all pass (existing `test_interest_celebration_shows_until_seen` must still pass).

- [ ] **Step 8: Rebuild CSS and commit**

```bash
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add app/main.py app/i18n.py app/templates/home.html app/templates/_celebration.html app/static/app.css docs/superpowers/specs/2026-09-19-sparziele-design.md tests/test_app.py
git commit -m "$(cat <<'EOF'
Show nearest goal on home and celebrate reached goals

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UFuTjGtkgsoXHa1DeZkUAd
EOF
)"
```

---

### Task 4: Parent read-only goal list

**Files:**
- Modify: `app/main.py` (`_parent_page`)
- Modify: `app/templates/parent.html`
- Modify: `app/i18n.py`
- Test: `tests/test_app.py` (append)

**Interfaces:**
- Consumes: `_goal_cards`, `visual`, `bar`, `ledger.list_goals`.
- Produces: template context `goals: dict[int, list[dict]]` keyed by kid id.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_parent_sees_kids_goals(family):
    login(family, 2, "1111")
    family.post("/ziele", data={"name": "Lego", "cents": 2000}, files={"photo": ("g.jpg", JPEG, "image/jpeg")})
    login(family, 1, "1234")
    page = family.get("/eltern").text
    assert "Lego" in page and "/ziele/1/bild" in page and "20,00 €" in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_parent_sees_kids_goals -v`
Expected: FAIL (`Lego` not in the parent page).

- [ ] **Step 3: Add the string**

In `app/i18n.py`, after `"par.rule.delete": "Löschen",`:

```python
        "par.goals": "Sparziele",
```

- [ ] **Step 4: Pass goals to the parent page**

In `_parent_page` in `app/main.py`, add this argument to the `render(...)` call:

```python
                  goals={k.id: _goal_cards(ledger.list_goals(s, k.id), acc) for k, acc in accounts},
```

- [ ] **Step 5: Update the template**

In `app/templates/parent.html`, add `{% from "_goal.html" import visual, bar %}` on the line below `{% extends "base.html" %}`. Then replace

```jinja
  </div>
  {% endfor %}
  <form method="post" action="/eltern/kinder" class="card space-y-3">
```

with

```jinja
    {% if goals[k.id] %}
    <div class="space-y-2">
      <h3 class="font-bold">🎯 {{ t('par.goals') }}</h3>
      {% for c in goals[k.id] %}
      <div class="flex items-center gap-3">
        {{ visual(c.goal, c.pct, 'h-16 w-16') }}
        <div class="flex-1 space-y-1">
          <div>{{ '✅ ' if c.goal.done_at }}{{ c.goal.name ~ ' · ' if c.goal.name }}{{ c.goal.target_cents|money }}</div>
          {{ bar(c.pct) }}
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
  {% endfor %}
  <form method="post" action="/eltern/kinder" class="card space-y-3">
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 7: Rebuild CSS and commit**

```bash
bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify
git add app/main.py app/i18n.py app/templates/parent.html app/static/app.css tests/test_app.py
git commit -m "$(cat <<'EOF'
Show kids' Sparziele read-only in the parent area

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UFuTjGtkgsoXHa1DeZkUAd
EOF
)"
```

---

### Task 5: Browser photo resize, preview and service-worker cache fix

The photo logic runs in the browser, so pytest cannot cover it; this task ends with a manual check.

**Files:**
- Modify: `app/static/app.js` (append)
- Modify: `app/static/sw.js` (cache version, activate handler)

- [ ] **Step 1: Add the resize handler**

Append to `app/static/app.js`:

```js
// Goal photo: shrink to a <= 512 px JPEG in the browser (phone photos are MBs), then show a preview.
document.addEventListener("change", async (e) => {
  const input = e.target.closest("[data-photo]");
  if (!input || !input.files[0]) return;
  try {
    const bmp = await createImageBitmap(input.files[0]);
    const scale = Math.min(1, 512 / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.8));
    const files = new DataTransfer();
    files.items.add(new File([blob], "goal.jpg", { type: "image/jpeg" }));
    input.files = files.files;
    const preview = input.closest("form").querySelector("[data-photo-preview]");
    preview.src = URL.createObjectURL(blob);
    preview.classList.remove("hidden");
  } catch {
    input.value = ""; // unreadable picture (e.g. unsupported format): send nothing rather than the raw file
  }
});
```

- [ ] **Step 2: Fix the service-worker asset cache**

`sw.js` serves `/static/` cache-first and never deletes old caches, so installed PWAs would keep the old `app.js` and `app.css` forever. In `app/static/sw.js`, change

```js
const ASSETS = "kb-assets-v1";
```

to

```js
const ASSETS = "kb-assets-v2";
```

and replace the activate listener

```js
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
```

with

```js
// Drop caches from older versions: caches.match() searches all of them, so a stale v1 would keep winning.
self.addEventListener("activate", (e) =>
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== ASSETS && k !== PAGES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  ),
);
```

- [ ] **Step 3: Run the test suite**

Run: `uv run pytest -v`
Expected: all pass (nothing here is Python).

- [ ] **Step 4: Manual browser check**

Run: `uv run uvicorn app.main:app --reload`, open the app in a browser (on a phone or tablet as well, if possible), log in as a kid, open Sparziele, and check:
1. Tapping "Foto machen" opens the camera or gallery chooser.
2. After picking a large photo (several MB), the preview appears.
3. Submitting with a price creates the goal, and the photo shows on the goals page and in the home card.
4. In the browser dev tools Network tab, the `POST /ziele` request is under 100 KB.
5. Dev tools, Application, Cache Storage: only `kb-assets-v2` and `kb-pages-v1` exist after a reload.
6. The photo appears grayscale at low progress and gets colour as the balance rises.

Report any failing point instead of committing.

- [ ] **Step 5: Commit**

```bash
git add app/static/app.js app/static/sw.js
git commit -m "$(cat <<'EOF'
Resize goal photos in the browser; purge stale service-worker caches

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UFuTjGtkgsoXHa1DeZkUAd
EOF
)"
```

---

### Task 6: Docs and final verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/ideas.md`

- [ ] **Step 1: Update CLAUDE.md**

In the "Ledger rules" list, add this bullet after the Festgeld bullet:

```markdown
- Sparziele (`Goal`) are progress-only: `goal_progress` / `goal_reached` compare the Giro balance to `target_cents`, nothing is reserved and there is no ledger booking. Finishing a goal ("Ziel geschafft!") only sets `done_at`; a parent books any real withdrawal by hand. Photos are JPEG BLOBs (max `MAX_PHOTO_BYTES`), resized in the browser and served by `/ziele/{id}/bild` to the owner or a parent only. The reached-goal celebration is dismissed by the same `POST /gesehen` as interest and allowance.
```

In the "Frontend" paragraph, append this sentence:

```markdown
`sw.js` caches `/static/` cache-first, so bump `ASSETS` whenever `app.js` or `app.css` change in a way installed clients must pick up (its `activate` handler deletes older cache names).
```

- [ ] **Step 2: Update the backlog**

In `docs/ideas.md`, replace the line

`Done or in progress: **Sparziele** (spec committed, implementation plan pending).`

with

`Done: **Sparziele** (spec and plan in \`docs/superpowers/\`).`

- [ ] **Step 3: Full verification**

Run: `uv run pytest -v`
Expected: all tests pass.

Run: `bin/tailwindcss -i app/tailwind.css -o app/static/app.css --minify && git status --short`
Expected: `app/static/app.css` unchanged (already rebuilt); nothing unexpected untracked.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/ideas.md
git commit -m "$(cat <<'EOF'
Document Sparziele in CLAUDE.md and mark it done in the backlog

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UFuTjGtkgsoXHa1DeZkUAd
EOF
)"
```
