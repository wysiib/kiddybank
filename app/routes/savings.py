"""The kid's term deposits ("treasure chest") and savings goals."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from .. import coins, goals, ledger, term_deposit
from ..i18n import format_money, t
from ..ledger import LedgerError
from ..models import Account, TermDepositProduct, Goal, User
from ..web import (
    active_deposits,
    current_user,
    db,
    get_today,
    issue_token,
    kid,
    need_module,
    redirect,
    render,
    use_token,
)

router = APIRouter()

GOAL_EMOJIS = ["🎯", "🧸", "🚲", "⚽", "🎮", "📚", "🎁", "✈️", "🐶", "🍦"]


# --- kid: term_deposit -----------------------------------------------------------------------------

def _deposit_row(a: Account, today: date, big: int, small: int) -> dict:
    """One open deposit with what its card draws: deposit and interest as coins, elapsed time as dots."""
    total, left = (a.maturity_date - a.opened_at).days, (a.maturity_date - today).days
    payout = term_deposit.term_deposit_payout(a)
    unit = coins.time_unit(total)
    return {"acc": a, "status": term_deposit.term_deposit_status(a, today), "days": left, "payout": payout,
            "big": big, "small": small, "deposit_coins": coins.coins(a.balance_cents, big),
            "interest_coins": coins.coins(payout[1], small), "unit": unit,
            "pips": (-(-total // unit), max(0, total - left) // unit)}


def _term_deposit_page(request: Request, s: Session, user: User, today: date, error: str | None = None,
                   gap: dict | None = None):
    deposits = active_deposits(s, user)
    if not user.term_deposits_enabled and not deposits:
        raise HTTPException(403, "err.module_off")
    big = coins.coin_unit([a.balance_cents for a in deposits])  # one unit for every card, so equal coins are equal money
    small = coins.coin_unit([term_deposit.term_deposit_payout(a)[1] for a in deposits], coins.SMALL_LADDER, coins.SMALL_CAP)
    rows = [_deposit_row(a, today, big, small) for a in deposits]
    products, view = term_deposit.offer_stacks(s, ledger.get_account(s, user.id, "checking"), 0)
    return render(request, "term_deposit.html", user=user, rows=rows, products=products, view=view, error=error, gap=gap,
                  tok=issue_token(request, "term_deposit"), days=ledger.get_account(s, user.id, "checking").payout_days)


@router.get("/term-deposits")
def term_deposit_page(request: Request, user: User = Depends(kid), s: Session = db,
                  today: date = Depends(get_today)):
    return _term_deposit_page(request, s, user, today)


@router.get("/term-deposits/preview")
def term_deposit_preview(request: Request, cents: int = 0, product_id: int = 0, user: User = Depends(kid),
                     s: Session = db):
    product = s.get(TermDepositProduct, product_id)
    products, view = term_deposit.offer_stacks(s, ledger.get_account(s, user.id, "checking"), cents)
    ctx = dict(oob=True, products=products, view=view)  # also refreshes the picture in every offer tile
    if cents <= 0 or not product:
        return render(request, "_preview.html", total=None, **ctx)
    return render(request, "_preview.html", total=term_deposit.payout(cents, product.rate_bp, product.term_days)[0], **ctx)


@router.post("/term-deposits/open")
def term_deposit_open(request: Request, cents: int = Form(0), product_id: int = Form(0), tok: str = Form(""),
                  user: User = Depends(kid), s: Session = db, today: date = Depends(get_today)):
    need_module(user, "term_deposits_enabled")
    if not use_token(request, "term_deposit", tok):  # a double tap: the first request already did it
        return redirect("/term-deposits")
    try:
        product = s.get(TermDepositProduct, product_id)
        if not product:
            raise LedgerError("err.term")
        term_deposit.open_term_deposit(s, ledger.get_account(s, user.id, "checking"), cents, product, today)
    except LedgerError as e:
        return _term_deposit_page(request, s, user, today, e.args[0], gap=(
            coins.shortfall(ledger.get_account(s, user.id, "checking").balance_cents, cents)
            if e.args[0] == "err.insufficient" else None))
    return redirect("/term-deposits")


@router.post("/term-deposits/{account_id}/collect")
def term_deposit_collect(request: Request, account_id: int, user: User = Depends(kid), s: Session = db,
                     today: date = Depends(get_today)):
    td = s.get(Account, account_id)  # deliberately not gated on term_deposits_enabled: never trap a kid's money
    if not td or td.user_id != user.id or td.type != "term_deposit":
        raise HTTPException(404)
    try:
        total, interest = term_deposit.collect_term_deposit(s, td, today)
    except LedgerError as e:
        return _term_deposit_page(request, s, user, today, e.args[0])
    return render(request, "done.html", user=user, emoji="🧰", msg=t("td.collected", total=format_money(total)),
                  lesson=t("td.ready.lesson", interest=format_money(interest)) + " " + t("td.collected.lesson"))


# --- kid: goals --------------------------------------------------------------------------------

def _goals_page(request: Request, s: Session, user: User, error: str | None = None):
    checking = ledger.get_account(s, user.id, "checking")
    cards = goals.cards(goals.list_goals(s, user.id), checking)
    active = [c for c in cards if not c["goal"].done_at]
    done = [c for c in reversed(cards) if c["goal"].done_at]
    return render(request, "goals.html", user=user, checking=checking, active=active, done=done,
                  can_add=len(active) < goals.MAX_ACTIVE_GOALS, emojis=GOAL_EMOJIS, error=error)


def _own_goal(s: Session, user: User, goal_id: int) -> Goal:
    g = s.get(Goal, goal_id)
    if not g or g.user_id != user.id:
        raise HTTPException(404)
    return g


@router.get("/goals")
def goals_page(request: Request, user: User = Depends(kid), s: Session = db):
    return _goals_page(request, s, user)


@router.post("/goals")
def goal_create(request: Request, name: str = Form(""), emoji: str = Form(""), cents: int = Form(0),
                photo: UploadFile | None = File(None), user: User = Depends(kid), s: Session = db,
                today: date = Depends(get_today)):
    data = photo.file.read(goals.MAX_PHOTO_BYTES + 1) if photo else b""  # bounded read, size is checked in the ledger
    try:
        goals.create_goal(s, user.id, name, emoji if emoji in GOAL_EMOJIS else GOAL_EMOJIS[0], cents, data or None, today)
    except LedgerError as e:
        return _goals_page(request, s, user, e.args[0])
    return redirect("/goals")


@router.post("/goals/{goal_id}/delete")
def goal_delete(goal_id: int, user: User = Depends(kid), s: Session = db):
    g = _own_goal(s, user, goal_id)
    if g.done_at:
        raise HTTPException(404)  # finished goals stay as a record
    s.delete(g)
    return redirect("/goals")


@router.post("/goals/{goal_id}/done")
def goal_finish(request: Request, goal_id: int, user: User = Depends(kid), s: Session = db,
                today: date = Depends(get_today)):
    g = _own_goal(s, user, goal_id)
    try:
        goals.finish_goal(g, ledger.get_account(s, user.id, "checking"), today)
    except LedgerError as e:
        return _goals_page(request, s, user, e.args[0])
    return render(request, "done.html", user=user, emoji="✅", msg=t("goal.done.msg"), lesson=t("goal.done.hint"))


@router.get("/goals/{goal_id}/photo")
def goal_photo(goal_id: int, user: User = Depends(current_user), s: Session = db):
    g = s.get(Goal, goal_id)
    if not g or not g.photo or (user.role != "parent" and g.user_id != user.id):
        raise HTTPException(404)
    return Response(g.photo, media_type="image/jpeg",
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"})
