"""The kid's everyday banking: home, statement, transfer, Einzahlen/Abheben."""

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import auth, coins, events, goals, ledger
from ..auth import verify_pin
from ..i18n import format_money, t
from ..ledger import LedgerError
from ..models import Account, User
from ..web import (
    active_deposits,
    db,
    get_today,
    issue_token,
    kid,
    redirect,
    render,
    use_token,
)

router = APIRouter()


# --- kid: home + statement ---------------------------------------------------------------------

def _events(s: Session, user: User) -> list[dict]:
    """Unseen interest / allowance (summed per kind) and reached goals, for the celebration screen."""
    totals: dict[str, int] = {}
    for tx in events.unseen_events(s, user.id):
        totals[tx.type] = totals.get(tx.type, 0) + tx.amount_cents
    return [{"type": k, "cents": v} for k, v in totals.items()] + [
        {"type": "goal", "goal": g} for g in goals.unseen_reached_goals(s, user.id)]


def _interest_hint(acc: Account, today: date) -> str:
    """Empty when the payout would round to 0 cents: nothing gets booked then, so don't promise it."""
    days, cents = ledger.next_interest(acc, today)
    if not cents:
        return ""
    when = t("home.interest.tomorrow") if days == 1 else t("home.interest.days", days=days)
    return t("home.interest", when=when, amount=format_money(cents))


@router.get("/home")
def home(request: Request, user: User = Depends(kid), s: Session = db, today: date = Depends(get_today)):
    giro = ledger.get_account(s, user.id, "giro")
    week = {k: v for k, v in ledger.week_summary(s, giro, today).items() if v}
    deposits = active_deposits(s, user)
    cards = goals.cards([g for g in goals.list_goals(s, user.id) if not g.done_at], giro)
    return render(request, "home.html", user=user, giro=giro, deposits=deposits, top=max(cards, key=lambda c: c["pct"], default=None), week=week,
                  events=_events(s, user), interest_hint=_interest_hint(giro, today), festgeld_visible=user.festgeld_enabled or bool(deposits))


@router.post("/gesehen")
def seen(user: User = Depends(kid), s: Session = db, today: date = Depends(get_today)):
    events.mark_seen(s, user.id, today)
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


def statement_rows(s: Session, acc: Account) -> list[dict]:
    return [{"emoji": e, "text": txt, "tx": tx, "delta": delta, "balance": bal}
            for tx, delta, bal in ledger.statement(s, acc)
            for e, txt in [_describe(s, acc, tx, delta)]]


@router.get("/konto/{account_id}")
def statement(request: Request, account_id: int, user: User = Depends(kid), s: Session = db):
    acc = s.get(Account, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(404)
    return render(request, "statement.html", user=user, acc=acc, rows=statement_rows(s, acc), who=None)


# --- kid: transfer -----------------------------------------------------------------------------

def _transfer_form(request: Request, s: Session, user: User, error: str | None = None):
    targets = [{"id": ledger.get_account(s, u.id, "giro").id, "avatar": u.avatar, "label": u.name}
               for u in s.exec(select(User).where(User.id != user.id).order_by(User.role.desc(), User.id)).all()]  # type: ignore[attr-defined]
    return render(request, "transfer.html", user=user, targets=targets, error=error,
                  giro=ledger.get_account(s, user.id, "giro"))


def _resolve(s: Session, user: User, to_id: int) -> tuple[Account, Account]:
    """(own giro, someone else's giro). Kids only ever move money out of their own Giro."""
    dst = s.get(Account, to_id)
    if not dst or dst.type != "giro" or dst.user_id == user.id:
        raise HTTPException(403, "err.forbidden")
    return ledger.get_account(s, user.id, "giro"), dst


@router.get("/ueberweisen")
def transfer_form(request: Request, user: User = Depends(kid), s: Session = db):
    return _transfer_form(request, s, user)


@router.post("/ueberweisen/pruefen")
def transfer_check(request: Request, to_id: int = Form(...), cents: int = Form(0),
                   user: User = Depends(kid), s: Session = db):
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


@router.post("/ueberweisen")
def transfer_do(request: Request, to_id: int = Form(...), cents: int = Form(...), tok: str = Form(""),
                user: User = Depends(kid), s: Session = db, today: date = Depends(get_today)):
    src, dst = _resolve(s, user, to_id)
    if not use_token(request, "transfer", tok):  # a double tap: the first request already did it
        return redirect("/home")
    try:
        ledger.transfer(s, src, dst, cents, today)
    except LedgerError as e:
        return _transfer_form(request, s, user, e.args[0])
    return render(request, "done.html", user=user, emoji="💸", msg=t("xfer.done"), lesson=t("xfer.lesson"))


# --- kid: cash in / out (real money changes hands with a parent, who confirms with their PIN) ---

CASH = ("einzahlen", "abheben")


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


@router.get("/bar/{kind}")
def cash_form(request: Request, kind: str, user: User = Depends(kid), s: Session = db):
    return _cash_page(request, s, user, kind)


@router.post("/bar/{kind}/pruefen")
def cash_check(request: Request, kind: str, cents: int = Form(0), user: User = Depends(kid),
               s: Session = db):
    giro = ledger.get_account(s, user.id, "giro")
    if cents <= 0:
        return _cash_page(request, s, user, kind, error="err.amount")
    if kind == "abheben" and cents > giro.balance_cents:
        return _cash_page(request, s, user, kind, error="err.insufficient")
    return _cash_page(request, s, user, kind, cents)


@router.post("/bar/{kind}")
def cash_do(request: Request, kind: str, cents: int = Form(...), pin: str = Form(...), tok: str = Form(""),
            user: User = Depends(kid),
            s: Session = db, today: date = Depends(get_today)):
    giro = ledger.get_account(s, user.id, "giro")
    parents = s.exec(select(User).where(User.role == "parent")).all()
    try:
        if kind not in CASH:
            raise LedgerError("err.amount")
        ledger.check_amount(cents)  # a negative amount would flip the direction of the booking
        if auth.locked(user):
            return _cash_page(request, s, user, kind, cents, "err.locked")
        if not any(verify_pin(pin, p.pin_hash) for p in parents):
            auth.pin_failed(user)
            return _cash_page(request, s, user, kind, cents, "cash.pin.wrong")
        auth.pin_ok(user)
        if not use_token(request, "cash", tok):  # a double tap: the first request already did it
            return redirect("/home")
        ledger.manual_booking(s, giro, cents if kind == "einzahlen" else -cents, today)
    except LedgerError as e:
        return _cash_page(request, s, user, kind if kind in CASH else "einzahlen", cents, e.args[0])
    key = "in" if kind == "einzahlen" else "out"
    return render(request, "done.html", user=user, emoji="📥" if key == "in" else "📤", msg=t(f"cash.{key}.done"),
                  lesson=t(f"cash.{key}.lesson"))
