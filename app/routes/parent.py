"""The parent area: kids, rates, offers, bookings, allowance rules."""

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import auth, festgeld, goals, ledger
from ..auth import hash_pin
from ..ledger import LedgerError
from ..models import Account, FestgeldProduct, RecurringRule, User
from ..web import (
    child_or_404,
    db,
    get_today,
    parent,
    parse_euro,
    parse_rate,
    redirect,
    render,
    valid_pin,
)
from .banking import statement_rows

router = APIRouter()

AVATARS = ["🐷", "🦊", "🐼", "🦁", "🐸", "🐙", "🦄", "🐯", "🐵", "🐰"]


# --- parents -----------------------------------------------------------------------------------

def _parent_page(request: Request, s: Session, user: User, error: str | None = None):
    kids = ledger.kids(s)
    accounts = [(k, ledger.get_account(s, k.id, "giro")) for k in kids]
    rules = [{"rule": r, "kid": s.get(User, s.get(Account, r.to_account_id).user_id)}
             for r in s.exec(select(RecurringRule).order_by(RecurringRule.id)).all()]
    return render(request, "parent.html", user=user, kids=kids, accounts=accounts,
                  giros={k.id: a for k, a in accounts}, periods=ledger.PAYOUT_PERIODS,
                  products=s.exec(select(FestgeldProduct).order_by(FestgeldProduct.term_days)).all(),
                  default_rate=ledger.DEFAULT_GIRO_BP, default_days=ledger.DEFAULT_PAYOUT_DAYS, rules=rules, avatars=AVATARS, error=error,
                  goals={k.id: goals.cards(goals.list_goals(s, k.id), acc) for k, acc in accounts})


@router.get("/eltern")
def parent_page(request: Request, user: User = Depends(parent), s: Session = db):
    return _parent_page(request, s, user)


@router.get("/eltern/kinder/{uid}/konto")
def kid_statement(request: Request, uid: int, user: User = Depends(parent), s: Session = db):
    kid_user = child_or_404(s, uid)
    acc = ledger.get_account(s, uid, "giro")
    return render(request, "statement.html", user=user, acc=acc, rows=statement_rows(s, acc), who=kid_user)


@router.post("/eltern/kinder")
def add_kid(request: Request, name: str = Form(...), pin: str = Form(...), avatar: str = Form("🐷"),
            rate: str = Form(""), period: int = Form(ledger.DEFAULT_PAYOUT_DAYS), festgeld: str | None = Form(None),
            stocks: str | None = Form(None), user: User = Depends(parent),
            s: Session = db, today: date = Depends(get_today)):
    try:
        valid_pin(pin)
        if not name.strip():
            raise LedgerError("err.name")
        k = ledger.create_user(s, name.strip(), "child", pin, avatar, today,
                               giro_rate_bp=parse_rate(rate, period) if rate.strip() else ledger.DEFAULT_GIRO_BP,
                               payout_days=period)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    k.festgeld_enabled, k.stocks_enabled = festgeld is not None, stocks is not None
    return redirect("/eltern")


@router.post("/eltern/kinder/{uid}")
def update_kid(request: Request, uid: int, rate: str = Form(""), period: int = Form(0),
                festgeld: str | None = Form(None), stocks: str | None = Form(None), avatar: str = Form(""),
                user: User = Depends(parent),
                s: Session = db, today: date = Depends(get_today)):
    k = child_or_404(s, uid)
    if rate.strip():
        try:
            giro = ledger.get_account(s, k.id, "giro")
            days = period or giro.payout_days
            ledger.set_giro_rate(s, giro, parse_rate(rate, days), today, days)
        except LedgerError as e:
            return _parent_page(request, s, user, e.args[0])
    k.festgeld_enabled, k.stocks_enabled = festgeld is not None, stocks is not None
    if avatar in AVATARS:
        k.avatar = avatar
    return redirect("/eltern")


@router.post("/eltern/kinder/{uid}/pin")
def reset_kid_pin(request: Request, uid: int, pin: str = Form(...), user: User = Depends(parent), s: Session = db):
    k = child_or_404(s, uid)
    try:
        valid_pin(pin)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    k.pin_hash = hash_pin(pin)
    auth.pin_ok(k)  # also lifts a lockout
    return redirect("/eltern")


@router.post("/eltern/buchen")
def book(request: Request, account_id: int = Form(...), amount: str = Form(...), note: str = Form(""),
         user: User = Depends(parent), s: Session = db, today: date = Depends(get_today)):
    acc = s.get(Account, account_id)
    if not acc or acc.type != "giro":
        raise HTTPException(404)
    try:
        ledger.manual_booking(s, acc, parse_euro(amount), today, note.strip())
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/eltern")


@router.post("/eltern/dauerauftrag")
def add_rule(request: Request, kid_id: int = Form(...), amount: str = Form(...), interval: str = Form(...),
             weekday: int = Form(0), monthday: int = Form(1), user: User = Depends(parent),
             s: Session = db, today: date = Depends(get_today)):
    giro = ledger.get_account(s, child_or_404(s, kid_id).id, "giro")
    try:
        ledger.add_rule(s, giro, parse_euro(amount), interval, weekday, monthday, today)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/eltern")


@router.post("/eltern/dauerauftrag/{rule_id}")
def edit_rule(request: Request, rule_id: int, amount: str = Form(...), interval: str = Form(...),
              weekday: int = Form(0), monthday: int = Form(1), user: User = Depends(parent),
              s: Session = db, today: date = Depends(get_today)):
    r = s.get(RecurringRule, rule_id)
    if not r:
        raise HTTPException(404)
    try:
        ledger.update_rule(s, r, parse_euro(amount), interval, weekday, monthday, today)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/eltern")


@router.post("/eltern/dauerauftrag/{rule_id}/loeschen")
def delete_rule(rule_id: int, user: User = Depends(parent), s: Session = db):
    if r := s.get(RecurringRule, rule_id):
        s.delete(r)
    return redirect("/eltern")


@router.post("/eltern/produkte/{product_id}/loeschen")
def delete_product(product_id: int, user: User = Depends(parent), s: Session = db):
    if p := s.get(FestgeldProduct, product_id):
        s.delete(p)  # safe: deposits snapshot name, rate and maturity, nothing references the product
    return redirect("/eltern")


@router.post("/eltern/produkte")
def add_product(request: Request, name: str = Form(...), days: int = Form(...), rate: str = Form(...),
                period: int = Form(ledger.DEFAULT_PAYOUT_DAYS), user: User = Depends(parent), s: Session = db):
    try:
        festgeld.add_product(s, name, days, parse_rate(rate, period), period)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/eltern")
