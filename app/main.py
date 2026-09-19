import os
import secrets
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from . import ledger
from . import auth
from .auth import verify_pin
from .i18n import LOCALE, format_date, format_money, format_percent, t
from .ledger import LedgerError
from .models import Account, FestgeldProduct, Goal, RecurringRule, User, make_engine

BASE = Path(__file__).parent
DB_URL = os.environ.get("KIDDYBANK_DB", "sqlite:///kiddybank.db")


PARENT_IDLE = 15 * 60  # seconds; kids stay signed in for the cookie's 30 days, parents on a shared tablet must not


def _secret() -> str:
    if key := os.environ.get("KIDDYBANK_SECRET"):
        return key
    f = Path(".session_secret")  # persisted so restarts don't log everyone out
    try:
        with os.fdopen(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:  # owner-only, no race between workers
            fh.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return f.read_text()


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=_secret(), same_site="lax", max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["t"] = t
templates.env.globals["locale"] = LOCALE
templates.env.filters["money"] = format_money
templates.env.filters["date"] = format_date
templates.env.filters["pct"] = lambda bp: format_percent(bp)
templates.env.filters["per"] = lambda bp, days: format_percent(ledger.period_bp(bp, days))  # annual bp -> "per week/month/year"

TOWER_DEMO_CENTS = 1000  # amount the Festgeld bars show until the kid has dialled one in
AVATARS = ["🐷", "🦊", "🐼", "🦁", "🐸", "🐙", "🦄", "🐯", "🐵", "🐰"]
GOAL_EMOJIS = ["🎯", "🧸", "🚲", "⚽", "🎮", "📚", "🎁", "✈️", "🐶", "🍦"]


# --- plumbing ----------------------------------------------------------------------------------

@lru_cache
def _engine():
    return make_engine(DB_URL)


def get_session():
    with Session(_engine()) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise


def get_today() -> date:
    return date.today()


def render(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


def sign_in(request: Request, user: User) -> None:
    request.session.clear()
    request.session["uid"] = user.id
    request.session["seen"] = time.time()


def current_user(request: Request, s: Session = Depends(get_session)) -> User:
    user = s.get(User, request.session.get("uid") or 0)
    if user and user.role == "parent" and time.time() - request.session.get("seen", 0) > PARENT_IDLE:
        request.session.clear()
        user = None
    if not user:
        raise HTTPException(303, headers={"Location": "/login"})
    request.session["seen"] = time.time()
    return user


def kid(user: User = Depends(current_user), s: Session = Depends(get_session), today: date = Depends(get_today)) -> User:
    if user.role != "child":
        raise HTTPException(303, headers={"Location": "/eltern"})
    ledger.catch_up_user(s, user.id, today)  # lazy interest / allowance, before anything else happens
    return user


def parent(user: User = Depends(current_user), s: Session = Depends(get_session), today: date = Depends(get_today)) -> User:
    if user.role != "parent":
        raise HTTPException(403, t("err.forbidden"))
    for k in s.exec(select(User).where(User.role == "child")).all():
        ledger.catch_up_user(s, k.id, today)
    return user


def issue_token(request: Request, purpose: str) -> str:
    """One-time token for a form that moves money: a double tap posts it twice, only the first one counts."""
    tok = request.session[f"tok:{purpose}"] = secrets.token_hex(8)
    return tok


def use_token(request: Request, purpose: str, tok: str) -> bool:
    return bool(tok) and request.session.pop(f"tok:{purpose}", None) == tok


def need_module(user: User, flag: str) -> None:
    if not getattr(user, flag):
        raise HTTPException(403, t("err.module_off"))


def own_accounts(s: Session, user: User, *types: str) -> list[Account]:
    q = select(Account).where(Account.user_id == user.id)
    if types:
        q = q.where(Account.type.in_(types))  # type: ignore[attr-defined]
    return list(s.exec(q.order_by(Account.id)).all())


def _decimal_x100(text: str, err: str) -> int:
    try:
        value = Decimal(text.strip().replace(",", "."))
        if not value.is_finite():  # "nan" / "inf" parse as Decimals but are no amounts
            raise InvalidOperation
        return int((value * 100).to_integral_value())
    except InvalidOperation:
        raise LedgerError(err) from None


def parse_euro(text: str) -> int:
    return _decimal_x100(text, "err.amount")


def parse_percent(text: str) -> int:
    """'2,5' -> 250 basis points."""
    return _decimal_x100(text, "err.rate")


def parse_rate(text: str, days: int) -> int:
    """'1' per 7 days -> annual basis points."""
    if days not in ledger.PAYOUT_PERIODS:
        raise LedgerError("err.rate")
    return ledger.annual_bp(parse_percent(text), days)


def valid_pin(pin: str) -> None:
    if not (len(pin) == 4 and pin.isdigit()):
        raise LedgerError("err.pin_format")


# --- login -------------------------------------------------------------------------------------

@app.get("/")
def index(request: Request, s: Session = Depends(get_session)):
    if not s.exec(select(User)).first():
        return redirect("/setup")
    user = s.get(User, request.session.get("uid") or 0)
    if not user:
        return redirect("/login")
    return redirect("/eltern" if user.role == "parent" else "/home")


@app.get("/setup")
def setup_form(request: Request, s: Session = Depends(get_session)):
    if s.exec(select(User)).first():
        return redirect("/")
    return render(request, "setup.html")


@app.post("/setup")
def setup(request: Request, name: str = Form(...), pin: str = Form(...), s: Session = Depends(get_session),
          today: date = Depends(get_today)):
    if s.exec(select(User)).first():
        return redirect("/")
    try:
        valid_pin(pin)
        if not name.strip():
            raise LedgerError("err.name")
    except LedgerError as e:
        return render(request, "setup.html", 400, error=e.args[0])
    u = ledger.create_user(s, name.strip(), "parent", pin, "👪", today)
    ledger.seed_default_products(s)
    sign_in(request, u)
    return redirect("/eltern")


@app.get("/login")
def login_picker(request: Request, s: Session = Depends(get_session)):
    return render(request, "login.html", users=s.exec(select(User).order_by(User.role.desc(), User.id)).all())  # type: ignore[attr-defined]


@app.get("/login/{uid}")
def pin_form(request: Request, uid: int, s: Session = Depends(get_session)):
    user = s.get(User, uid)
    if not user:
        return redirect("/login")
    return render(request, "pin.html", who=user)


@app.post("/login/{uid}")
def pin_submit(request: Request, uid: int, pin: str = Form(...), s: Session = Depends(get_session)):
    user = s.get(User, uid)
    if not user:
        return redirect("/login")
    if auth.locked(user):
        return render(request, "pin.html", 200, who=user, error="err.locked")
    if not verify_pin(pin, user.pin_hash):
        auth.pin_failed(user)
        return render(request, "pin.html", 200, who=user, error="login.pin.wrong")
    auth.pin_ok(user)
    sign_in(request, user)
    return redirect("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.get("/offline")
def offline(request: Request):
    return render(request, "offline.html")


@app.get("/sw.js")
def service_worker():
    # served from the root so its scope covers the whole app
    return FileResponse(BASE / "static" / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# --- kid: home + statement ---------------------------------------------------------------------

def _events(s: Session, user: User) -> list[dict]:
    """Unseen interest / allowance (summed per kind) and reached goals, for the celebration screen."""
    totals: dict[str, int] = {}
    for tx in ledger.unseen_events(s, user.id):
        totals[tx.type] = totals.get(tx.type, 0) + tx.amount_cents
    return [{"type": k, "cents": v} for k, v in totals.items()] + [
        {"type": "goal", "goal": g} for g in ledger.unseen_reached_goals(s, user.id)]


def _interest_hint(acc: Account, today: date) -> str:
    """Empty when the payout would round to 0 cents: nothing gets booked then, so don't promise it."""
    days, cents = ledger.next_interest(acc, today)
    if not cents:
        return ""
    when = t("home.interest.tomorrow") if days == 1 else t("home.interest.days", days=days)
    return t("home.interest", when=when, amount=format_money(cents))


@app.get("/home")
def home(request: Request, user: User = Depends(kid), s: Session = Depends(get_session), today: date = Depends(get_today)):
    giro = ledger.get_account(s, user.id, "giro")
    week = {k: v for k, v in ledger.week_summary(s, giro, today).items() if v}
    deposits = [a for a in own_accounts(s, user, "festgeld") if not a.collected_at]
    cards = _goal_cards([g for g in ledger.list_goals(s, user.id) if not g.done_at], giro)
    return render(request, "home.html", user=user, giro=giro, deposits=deposits, top=max(cards, key=lambda c: c["pct"], default=None), week=week,
                  events=_events(s, user), interest_hint=_interest_hint(giro, today), festgeld_visible=user.festgeld_enabled or bool(deposits))


@app.post("/gesehen")
def seen(user: User = Depends(kid), s: Session = Depends(get_session), today: date = Depends(get_today)):
    ledger.mark_seen(s, user.id, today)
    return redirect("/home")


def _describe(s: Session, acc: Account, tx, delta: int) -> tuple[str, str]:
    """(emoji, text) for one statement line."""
    inbound = delta > 0
    if tx.type in ("zins", "dauerauftrag"):
        return ("✨" if tx.type == "zins" else "🗓️"), t(f"tx.{tx.type}")
    if tx.type in ("aktienkauf", "aktienverkauf"):
        return "📈", f"{t(f'tx.{tx.type}')} ({tx.note})"
    if tx.type == "festgeld":
        return "🧰", t("tx.festgeld.in" if inbound else "tx.festgeld.out")
    other_id = tx.from_account_id if inbound else tx.to_account_id
    if other_id is None:
        return "👪", t("tx.parent.in" if inbound else "tx.parent.out")
    name = s.get(User, s.get(Account, other_id).user_id).name
    return "💸", t("tx.transfer.in" if inbound else "tx.transfer.out", name=name)


def _statement_rows(s: Session, acc: Account) -> list[dict]:
    return [{"emoji": e, "text": txt, "tx": tx, "delta": delta, "balance": bal}
            for tx, delta, bal in ledger.statement(s, acc)
            for e, txt in [_describe(s, acc, tx, delta)]]


@app.get("/konto/{account_id}")
def statement(request: Request, account_id: int, user: User = Depends(kid), s: Session = Depends(get_session)):
    acc = s.get(Account, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(404)
    return render(request, "statement.html", user=user, acc=acc, rows=_statement_rows(s, acc), who=None)


# --- kid: transfer -----------------------------------------------------------------------------

def _transfer_form(request: Request, s: Session, user: User, error: str | None = None):
    targets = [{"id": ledger.get_account(s, u.id, "giro").id, "avatar": u.avatar, "label": u.name}
               for u in s.exec(select(User).where(User.id != user.id).order_by(User.role.desc(), User.id)).all()]  # type: ignore[attr-defined]
    return render(request, "transfer.html", 200 if not error else 400, user=user, targets=targets, error=error,
                  giro=ledger.get_account(s, user.id, "giro"))


def _resolve(s: Session, user: User, to_id: int) -> tuple[Account, Account]:
    """(own giro, someone else's giro). Kids only ever move money out of their own Giro."""
    dst = s.get(Account, to_id)
    if not dst or dst.type != "giro" or dst.user_id == user.id:
        raise HTTPException(403, t("err.forbidden"))
    return ledger.get_account(s, user.id, "giro"), dst


@app.get("/ueberweisen")
def transfer_form(request: Request, user: User = Depends(kid), s: Session = Depends(get_session)):
    return _transfer_form(request, s, user)


@app.post("/ueberweisen/pruefen")
def transfer_check(request: Request, to_id: int = Form(...), cents: int = Form(0),
                   user: User = Depends(kid), s: Session = Depends(get_session)):
    src, dst = _resolve(s, user, to_id)
    try:
        if cents <= 0:
            raise LedgerError("err.amount")
        if cents > src.balance_cents:
            raise LedgerError("err.insufficient")
    except LedgerError as e:
        return _transfer_form(request, s, user, e.args[0])
    return render(request, "transfer_confirm.html", user=user, src=src, dst=dst, cents=cents,
                  to_name=s.get(User, dst.user_id).name, tok=issue_token(request, "transfer"))


@app.post("/ueberweisen")
def transfer_do(request: Request, to_id: int = Form(...), cents: int = Form(...), tok: str = Form(""),
                user: User = Depends(kid), s: Session = Depends(get_session), today: date = Depends(get_today)):
    src, dst = _resolve(s, user, to_id)
    if not use_token(request, "transfer", tok):  # a double tap: the first request already did it
        return redirect("/home")
    try:
        ledger.transfer(s, src, dst, cents, today)
    except LedgerError as e:
        s.rollback()
        return _transfer_form(request, s, user, e.args[0])
    return render(request, "done.html", user=user, emoji="💸", msg=t("xfer.done"), lesson=t("xfer.lesson"))


# --- kid: cash in / out (real money changes hands with a parent, who confirms with their PIN) ---

CASH = ("einzahlen", "abheben")


def _cash_page(request: Request, s: Session, user: User, kind: str, cents: int | None = None, error: str | None = None):
    if kind not in CASH:
        raise HTTPException(404)
    giro = ledger.get_account(s, user.id, "giro")
    lost = ledger.interest_cents(cents or 0, giro.interest_rate_bp, giro.payout_days) if kind == "abheben" else 0
    return render(request, "cash.html", 400 if error else 200, user=user, giro=giro, kind=kind, cents=cents,
                  lost=lost, error=error, tok=issue_token(request, "cash") if cents else None)


@app.get("/bar/{kind}")
def cash_form(request: Request, kind: str, user: User = Depends(kid), s: Session = Depends(get_session)):
    return _cash_page(request, s, user, kind)


@app.post("/bar/{kind}/pruefen")
def cash_check(request: Request, kind: str, cents: int = Form(0), user: User = Depends(kid),
               s: Session = Depends(get_session)):
    giro = ledger.get_account(s, user.id, "giro")
    if cents <= 0:
        return _cash_page(request, s, user, kind, error="err.amount")
    if kind == "abheben" and cents > giro.balance_cents:
        return _cash_page(request, s, user, kind, error="err.insufficient")
    return _cash_page(request, s, user, kind, cents)


@app.post("/bar/{kind}")
def cash_do(request: Request, kind: str, cents: int = Form(...), pin: str = Form(...), tok: str = Form(""),
            user: User = Depends(kid),
            s: Session = Depends(get_session), today: date = Depends(get_today)):
    giro = ledger.get_account(s, user.id, "giro")
    parents = s.exec(select(User).where(User.role == "parent")).all()
    try:
        if kind not in CASH:
            raise LedgerError("err.amount")
        ledger.check_amount(cents)  # a negative amount would flip the direction of the booking
        if auth.locked(user):
            return _cash_page(request, s, user, kind, cents, "err.locked")
        if not any(verify_pin(pin, p.pin_hash) for p in parents):
            auth.pin_failed(user)  # returned, not raised: the rollback below would throw the count away
            return _cash_page(request, s, user, kind, cents, "cash.pin.wrong")
        auth.pin_ok(user)
        if not use_token(request, "cash", tok):  # a double tap: the first request already did it
            return redirect("/home")
        ledger.manual_booking(s, giro, cents if kind == "einzahlen" else -cents, today)
    except LedgerError as e:
        s.rollback()
        return _cash_page(request, s, user, kind if kind in CASH else "einzahlen", cents, e.args[0])
    key = "in" if kind == "einzahlen" else "out"
    return render(request, "done.html", user=user, emoji="📥" if key == "in" else "📤", msg=t(f"cash.{key}.done"),
                  lesson=t(f"cash.{key}.lesson"))


# --- kid: festgeld -----------------------------------------------------------------------------

@app.get("/festgeld")
def festgeld_page(request: Request, user: User = Depends(kid), s: Session = Depends(get_session),
                  today: date = Depends(get_today), error: str | None = None):
    deposits = [a for a in own_accounts(s, user, "festgeld") if not a.collected_at]
    if not user.festgeld_enabled and not deposits:
        raise HTTPException(403, t("err.module_off"))
    rows = [{"acc": a, "status": ledger.festgeld_status(a, today), "days": (a.maturity_date - today).days,
             "payout": ledger.festgeld_payout(a)} for a in deposits]
    products, towers = offer_towers(s, user, 0)
    return render(request, "festgeld.html", user=user, rows=rows, products=products, towers=towers, error=error,
                  tok=issue_token(request, "festgeld"), days=ledger.get_account(s, user.id, "giro").payout_days)


def offer_towers(s: Session, user: User, cents: int):
    """The offers plus, per offer, the bonus a Giro vs. that offer would pay on `cents` and the bar heights (0-100, one shared scale)."""
    products = s.exec(select(FestgeldProduct).order_by(FestgeldProduct.term_days)).all()
    giro_bp = ledger.get_account(s, user.id, "giro").interest_rate_bp
    cents = cents if cents > 0 else TOWER_DEMO_CENTS
    bonus = {p.id: (ledger.interest_cents(cents, giro_bp, p.term_days), ledger.interest_cents(cents, p.rate_bp, p.term_days))
             for p in products}
    top = max((b for pair in bonus.values() for b in pair), default=0)
    height = lambda b: max(8, b * 100 // top) if b else 0  # a tiny bonus still gets a visible stub
    return products, {pid: {"giro": g, "fg": f, "giro_h": height(g), "fg_h": height(f)} for pid, (g, f) in bonus.items()}


@app.get("/festgeld/vorschau")
def festgeld_preview(request: Request, cents: int = 0, product_id: int = 0, user: User = Depends(kid),
                     s: Session = Depends(get_session), today: date = Depends(get_today)):
    product = s.get(FestgeldProduct, product_id)
    products, towers = offer_towers(s, user, cents)
    ctx = dict(oob=True, products=products, towers=towers)  # also refreshes the bars in every offer tile
    if cents <= 0 or not product:
        return render(request, "_preview.html", total=None, **ctx)
    fake = Account(user_id=user.id, type="festgeld", balance_cents=cents, interest_rate_bp=product.rate_bp,
                   last_updated=today, interest_paid_on=today, opened_at=today,
                   maturity_date=today + timedelta(days=product.term_days))
    return render(request, "_preview.html", total=ledger.festgeld_payout(fake)[0], **ctx)


@app.post("/festgeld/oeffnen")
def festgeld_open(request: Request, cents: int = Form(0), product_id: int = Form(0), tok: str = Form(""),
                  user: User = Depends(kid), s: Session = Depends(get_session), today: date = Depends(get_today)):
    need_module(user, "festgeld_enabled")
    if not use_token(request, "festgeld", tok):  # a double tap: the first request already did it
        return redirect("/festgeld")
    try:
        product = s.get(FestgeldProduct, product_id)
        if not product:
            raise LedgerError("err.term")
        ledger.open_festgeld(s, ledger.get_account(s, user.id, "giro"), cents, product, today)
    except LedgerError as e:
        s.rollback()
        return redirect(f"/festgeld?error={e.args[0]}")
    return redirect("/festgeld")


@app.post("/festgeld/{account_id}/abholen")
def festgeld_collect(request: Request, account_id: int, user: User = Depends(kid), s: Session = Depends(get_session),
                     today: date = Depends(get_today)):
    fg = s.get(Account, account_id)  # deliberately not gated on festgeld_enabled: never trap a kid's money
    if not fg or fg.user_id != user.id or fg.type != "festgeld":
        raise HTTPException(404)
    interest = ledger.festgeld_payout(fg)[1]
    try:
        total = ledger.collect_festgeld(s, fg, today)
    except LedgerError as e:
        s.rollback()
        return redirect(f"/festgeld?error={e.args[0]}")
    return render(request, "done.html", user=user, emoji="🧰", msg=t("fg.collected", total=format_money(total)),
                  lesson=t("fg.ready.lesson", interest=format_money(interest)) + " " + t("fg.collected.lesson"))


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
                photo: UploadFile | None = File(None), user: User = Depends(kid), s: Session = Depends(get_session),
                today: date = Depends(get_today)):
    data = photo.file.read(ledger.MAX_PHOTO_BYTES + 1) if photo else b""  # bounded read, size is checked in the ledger
    try:
        ledger.create_goal(s, user.id, name, emoji if emoji in GOAL_EMOJIS else GOAL_EMOJIS[0], cents, data or None, today)
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
def goal_finish(request: Request, goal_id: int, user: User = Depends(kid), s: Session = Depends(get_session),
                today: date = Depends(get_today)):
    g = _own_goal(s, user, goal_id)
    try:
        ledger.finish_goal(g, ledger.get_account(s, user.id, "giro"), today)
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


# --- parents -----------------------------------------------------------------------------------

def _parent_page(request: Request, s: Session, user: User, error: str | None = None):
    kids = s.exec(select(User).where(User.role == "child").order_by(User.id)).all()
    accounts = [(k, ledger.get_account(s, k.id, "giro")) for k in kids]
    rules = [{"rule": r, "kid": s.get(User, s.get(Account, r.to_account_id).user_id)}
             for r in s.exec(select(RecurringRule).order_by(RecurringRule.id)).all()]
    return render(request, "parent.html", 200 if not error else 400, user=user, kids=kids, accounts=accounts,
                  giros={k.id: a for k, a in accounts}, periods=ledger.PAYOUT_PERIODS,
                  products=s.exec(select(FestgeldProduct).order_by(FestgeldProduct.term_days)).all(),
                  default_rate=ledger.DEFAULT_GIRO_BP, default_days=ledger.DEFAULT_PAYOUT_DAYS, rules=rules, avatars=AVATARS, error=error,
                  goals={k.id: _goal_cards(ledger.list_goals(s, k.id), acc) for k, acc in accounts})


@app.get("/eltern")
def parent_page(request: Request, user: User = Depends(parent), s: Session = Depends(get_session)):
    return _parent_page(request, s, user)


@app.get("/eltern/kinder/{uid}/konto")
def kid_statement(request: Request, uid: int, user: User = Depends(parent), s: Session = Depends(get_session)):
    kid_user = s.get(User, uid)
    if not kid_user or kid_user.role != "child":
        raise HTTPException(404)
    acc = ledger.get_account(s, uid, "giro")
    return render(request, "statement.html", user=user, acc=acc, rows=_statement_rows(s, acc), who=kid_user)


@app.post("/eltern/kinder")
def add_kid(request: Request, name: str = Form(...), pin: str = Form(...), avatar: str = Form("🐷"),
            rate: str = Form(""), period: int = Form(ledger.DEFAULT_PAYOUT_DAYS), festgeld: str | None = Form(None),
            stocks: str | None = Form(None), user: User = Depends(parent),
            s: Session = Depends(get_session), today: date = Depends(get_today)):
    try:
        valid_pin(pin)
        if not name.strip():
            raise LedgerError("err.name")
        k = ledger.create_user(s, name.strip(), "child", pin, avatar, today,
                               giro_rate_bp=parse_rate(rate, period) if rate.strip() else ledger.DEFAULT_GIRO_BP,
                               payout_days=period)
    except LedgerError as e:
        s.rollback()
        return _parent_page(request, s, user, e.args[0])
    k.festgeld_enabled, k.stocks_enabled = festgeld is not None, stocks is not None
    return redirect("/eltern")


@app.post("/eltern/kinder/{uid}/module")
def set_modules(request: Request, uid: int, rate: str = Form(""), period: int = Form(0),
                festgeld: str | None = Form(None), stocks: str | None = Form(None), avatar: str = Form(""),
                user: User = Depends(parent),
                s: Session = Depends(get_session), today: date = Depends(get_today)):
    k = s.get(User, uid)
    if not k or k.role != "child":
        raise HTTPException(404)
    if rate.strip():
        try:
            giro = ledger.get_account(s, k.id, "giro")
            days = period or giro.payout_days
            ledger.set_giro_rate(s, giro, parse_rate(rate, days), today, days)
        except LedgerError as e:
            s.rollback()
            return _parent_page(request, s, user, e.args[0])
    k.festgeld_enabled, k.stocks_enabled = festgeld is not None, stocks is not None
    if avatar in AVATARS:
        k.avatar = avatar
    return redirect("/eltern")


@app.post("/eltern/buchen")
def book(request: Request, account_id: int = Form(...), amount: str = Form(...), note: str = Form(""),
         user: User = Depends(parent), s: Session = Depends(get_session), today: date = Depends(get_today)):
    acc = s.get(Account, account_id)
    if not acc or acc.type != "giro":
        raise HTTPException(404)
    try:
        ledger.manual_booking(s, acc, parse_euro(amount), today, note.strip())
    except LedgerError as e:
        s.rollback()
        return _parent_page(request, s, user, e.args[0])
    return redirect("/eltern")


def _rule_input(amount: str, interval: str, weekday: int, monthday: int) -> tuple[int, str, int]:
    cents = parse_euro(amount)
    if cents <= 0 or interval not in ("weekly", "monthly") or not (0 <= weekday <= 6 and 1 <= monthday <= 28):
        raise LedgerError("err.amount")
    return cents, interval, weekday if interval == "weekly" else monthday


@app.post("/eltern/dauerauftrag")
def add_rule(request: Request, kid_id: int = Form(...), amount: str = Form(...), interval: str = Form(...),
             weekday: int = Form(0), monthday: int = Form(1), user: User = Depends(parent),
             s: Session = Depends(get_session), today: date = Depends(get_today)):
    try:
        cents, interval, anchor = _rule_input(amount, interval, weekday, monthday)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    s.add(RecurringRule(from_account_id=None, to_account_id=ledger.get_account(s, kid_id, "giro").id,
                        amount_cents=cents, interval=interval, next_run=ledger.first_run(interval, anchor, today)))
    return redirect("/eltern")


@app.post("/eltern/dauerauftrag/{rule_id}")
def edit_rule(request: Request, rule_id: int, amount: str = Form(...), interval: str = Form(...),
              weekday: int = Form(0), monthday: int = Form(1), user: User = Depends(parent),
              s: Session = Depends(get_session), today: date = Depends(get_today)):
    r = s.get(RecurringRule, rule_id)
    if not r:
        raise HTTPException(404)
    try:
        cents, interval, anchor = _rule_input(amount, interval, weekday, monthday)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    # pay out anything already due under the old schedule before moving next_run
    ledger.ensure_up_to_date(s, s.get(Account, r.to_account_id), today)
    r.amount_cents, r.interval, r.next_run = cents, interval, ledger.first_run(interval, anchor, today)
    return redirect("/eltern")


@app.post("/eltern/dauerauftrag/{rule_id}/loeschen")
def delete_rule(rule_id: int, user: User = Depends(parent), s: Session = Depends(get_session)):
    if r := s.get(RecurringRule, rule_id):
        s.delete(r)
    return redirect("/eltern")


@app.post("/eltern/produkte/{product_id}/loeschen")
def delete_product(product_id: int, user: User = Depends(parent), s: Session = Depends(get_session)):
    if p := s.get(FestgeldProduct, product_id):
        s.delete(p)  # safe: deposits snapshot name, rate and maturity, nothing references the product
    return redirect("/eltern")


@app.post("/eltern/produkte")
def add_product(request: Request, name: str = Form(...), days: int = Form(...), rate: str = Form(...),
                period: int = Form(ledger.DEFAULT_PAYOUT_DAYS), user: User = Depends(parent), s: Session = Depends(get_session)):
    try:
        ledger.add_product(s, name, days, parse_rate(rate, period), period)
    except LedgerError as e:
        s.rollback()
        return _parent_page(request, s, user, e.args[0])
    return redirect("/eltern")
