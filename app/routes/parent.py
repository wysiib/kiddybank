"""The parent area: kids, rates, offers, bookings, allowance rules."""

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import auth, goals, ledger, term_deposit
from ..auth import hash_pin
from ..ledger import LedgerError
from ..models import Account, TermDepositProduct, Goal, RecurringRule, User
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
    accounts = [(k, ledger.get_account(s, k.id, "checking")) for k in kids]
    rules = [{"rule": r, "kid": s.get(User, s.get(Account, r.to_account_id).user_id)}
             for r in s.exec(select(RecurringRule).order_by(RecurringRule.id)).all()]
    return render(request, "parent.html", user=user, kids=kids, accounts=accounts,
                  checking_accounts={k.id: a for k, a in accounts}, periods=ledger.PAYOUT_PERIODS,
                  products=s.exec(select(TermDepositProduct).order_by(TermDepositProduct.term_days)).all(),
                  default_rate=ledger.DEFAULT_CHECKING_BP, default_days=ledger.DEFAULT_PAYOUT_DAYS, rules=rules, avatars=AVATARS, error=error,
                  goals={k.id: goals.cards(goals.list_goals(s, k.id), acc) for k, acc in accounts})


@router.get("/parent")
def parent_page(request: Request, user: User = Depends(parent), s: Session = db):
    return _parent_page(request, s, user)


@router.get("/parent/kids/{uid}/account")
def kid_statement(request: Request, uid: int, user: User = Depends(parent), s: Session = db):
    kid_user = child_or_404(s, uid)
    acc = ledger.get_account(s, uid, "checking")
    return render(request, "statement.html", user=user, acc=acc, rows=statement_rows(s, acc), who=kid_user)


@router.post("/parent/kids")
def add_kid(request: Request, name: str = Form(...), pin: str = Form(...), avatar: str = Form("🐷"),
            rate: str = Form(""), period: int = Form(ledger.DEFAULT_PAYOUT_DAYS), term_deposits: str | None = Form(None),
            stocks: str | None = Form(None), user: User = Depends(parent),
            s: Session = db, today: date = Depends(get_today)):
    try:
        valid_pin(pin)
        if not name.strip():
            raise LedgerError("err.name")
        k = ledger.create_user(s, name.strip(), "child", pin, avatar, today,
                               checking_rate_bp=parse_rate(rate, period) if rate.strip() else ledger.DEFAULT_CHECKING_BP,
                               payout_days=period)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    k.term_deposits_enabled, k.stocks_enabled = term_deposits is not None, stocks is not None
    return redirect("/parent")


@router.post("/parent/kids/{uid}")
def update_kid(request: Request, uid: int, rate: str = Form(""), period: int = Form(0),
                term_deposits: str | None = Form(None), stocks: str | None = Form(None), avatar: str = Form(""),
                user: User = Depends(parent),
                s: Session = db, today: date = Depends(get_today)):
    k = child_or_404(s, uid)
    if rate.strip():
        try:
            checking = ledger.get_account(s, k.id, "checking")
            days = period or checking.payout_days
            ledger.set_checking_rate(s, checking, parse_rate(rate, days), today, days)
        except LedgerError as e:
            return _parent_page(request, s, user, e.args[0])
    k.term_deposits_enabled, k.stocks_enabled = term_deposits is not None, stocks is not None
    if avatar in AVATARS:
        k.avatar = avatar
    return redirect("/parent")


@router.post("/parent/kids/{uid}/pin")
def reset_kid_pin(request: Request, uid: int, pin: str = Form(...), user: User = Depends(parent), s: Session = db):
    k = child_or_404(s, uid)
    try:
        valid_pin(pin)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    k.pin_hash = hash_pin(pin)
    auth.pin_ok(k)  # also lifts a lockout
    return redirect("/parent")


@router.post("/parent/book")
def book(request: Request, account_id: int = Form(...), amount: str = Form(...), note: str = Form(""),
         user: User = Depends(parent), s: Session = db, today: date = Depends(get_today)):
    acc = s.get(Account, account_id)
    if not acc or acc.type != "checking":
        raise HTTPException(404)
    try:
        ledger.manual_booking(s, acc, parse_euro(amount), today, note.strip())
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/parent")


@router.post("/parent/rules")
def add_rule(request: Request, kid_id: int = Form(...), amount: str = Form(...), interval: str = Form(...),
             weekday: int = Form(0), monthday: int = Form(1), user: User = Depends(parent),
             s: Session = db, today: date = Depends(get_today)):
    checking = ledger.get_account(s, child_or_404(s, kid_id).id, "checking")
    try:
        ledger.add_rule(s, checking, parse_euro(amount), interval, weekday, monthday, today)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/parent")


@router.post("/parent/rules/{rule_id}")
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
    return redirect("/parent")


@router.post("/parent/rules/{rule_id}/delete")
def delete_rule(rule_id: int, user: User = Depends(parent), s: Session = db):
    if r := s.get(RecurringRule, rule_id):
        s.delete(r)
    return redirect("/parent")


@router.post("/parent/goals/{goal_id}/delete")
def delete_goal(goal_id: int, user: User = Depends(parent), s: Session = db):
    if g := s.get(Goal, goal_id):
        s.delete(g)  # progress-only, nothing was booked; unlike the kid, a parent may also clear finished goals
    return redirect("/parent")


@router.post("/parent/products/{product_id}/delete")
def delete_product(product_id: int, user: User = Depends(parent), s: Session = db):
    if p := s.get(TermDepositProduct, product_id):
        s.delete(p)  # safe: deposits snapshot name, rate and maturity, nothing references the product
    return redirect("/parent")


@router.post("/parent/products")
def add_product(request: Request, name: str = Form(...), days: int = Form(...), rate: str = Form(...),
                period: int = Form(ledger.DEFAULT_PAYOUT_DAYS), user: User = Depends(parent), s: Session = db):
    try:
        term_deposit.add_product(s, name, days, parse_rate(rate, period), period)
    except LedgerError as e:
        return _parent_page(request, s, user, e.args[0])
    return redirect("/parent")
