"""The kid's Schatztruhe (Festgeld) and Sparziele."""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from .. import coins, festgeld, goals, ledger
from ..i18n import format_money, t
from ..ledger import LedgerError
from ..models import Account, FestgeldProduct, Goal, User
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


# --- kid: festgeld -----------------------------------------------------------------------------

def _deposit_row(a: Account, today: date) -> dict:
    """One open deposit with what its card draws: deposit and interest as coins, elapsed time as dots."""
    total, left = (a.maturity_date - a.opened_at).days, (a.maturity_date - today).days
    payout = festgeld.festgeld_payout(a)
    big = coins.coin_unit([a.balance_cents])
    small = coins.coin_unit([payout[1]], coins.SMALL_LADDER, coins.SMALL_CAP)
    unit = coins.time_unit(total)
    return {"acc": a, "status": festgeld.festgeld_status(a, today), "days": left, "payout": payout,
            "big": big, "small": small, "deposit_coins": coins.coins(a.balance_cents, big),
            "interest_coins": coins.coins(payout[1], small), "unit": unit,
            "pips": (-(-total // unit), max(0, total - left) // unit)}


def _festgeld_page(request: Request, s: Session, user: User, today: date, error: str | None = None):
    deposits = active_deposits(s, user)
    if not user.festgeld_enabled and not deposits:
        raise HTTPException(403, "err.module_off")
    rows = [_deposit_row(a, today) for a in deposits]
    products, view = festgeld.offer_stacks(s, ledger.get_account(s, user.id, "giro"), 0)
    return render(request, "festgeld.html", user=user, rows=rows, products=products, view=view, error=error,
                  tok=issue_token(request, "festgeld"), days=ledger.get_account(s, user.id, "giro").payout_days)


@router.get("/festgeld")
def festgeld_page(request: Request, user: User = Depends(kid), s: Session = db,
                  today: date = Depends(get_today)):
    return _festgeld_page(request, s, user, today)


@router.get("/festgeld/vorschau")
def festgeld_preview(request: Request, cents: int = 0, product_id: int = 0, user: User = Depends(kid),
                     s: Session = db):
    product = s.get(FestgeldProduct, product_id)
    products, view = festgeld.offer_stacks(s, ledger.get_account(s, user.id, "giro"), cents)
    ctx = dict(oob=True, products=products, view=view)  # also refreshes the picture in every offer tile
    if cents <= 0 or not product:
        return render(request, "_preview.html", total=None, **ctx)
    return render(request, "_preview.html", total=festgeld.payout(cents, product.rate_bp, product.term_days)[0], **ctx)


@router.post("/festgeld/oeffnen")
def festgeld_open(request: Request, cents: int = Form(0), product_id: int = Form(0), tok: str = Form(""),
                  user: User = Depends(kid), s: Session = db, today: date = Depends(get_today)):
    need_module(user, "festgeld_enabled")
    if not use_token(request, "festgeld", tok):  # a double tap: the first request already did it
        return redirect("/festgeld")
    try:
        product = s.get(FestgeldProduct, product_id)
        if not product:
            raise LedgerError("err.term")
        festgeld.open_festgeld(s, ledger.get_account(s, user.id, "giro"), cents, product, today)
    except LedgerError as e:
        return _festgeld_page(request, s, user, today, e.args[0])
    return redirect("/festgeld")


@router.post("/festgeld/{account_id}/abholen")
def festgeld_collect(request: Request, account_id: int, user: User = Depends(kid), s: Session = db,
                     today: date = Depends(get_today)):
    fg = s.get(Account, account_id)  # deliberately not gated on festgeld_enabled: never trap a kid's money
    if not fg or fg.user_id != user.id or fg.type != "festgeld":
        raise HTTPException(404)
    try:
        total, interest = festgeld.collect_festgeld(s, fg, today)
    except LedgerError as e:
        return _festgeld_page(request, s, user, today, e.args[0])
    return render(request, "done.html", user=user, emoji="🧰", msg=t("fg.collected", total=format_money(total)),
                  lesson=t("fg.ready.lesson", interest=format_money(interest)) + " " + t("fg.collected.lesson"))


# --- kid: goals --------------------------------------------------------------------------------

def _goals_page(request: Request, s: Session, user: User, error: str | None = None):
    giro = ledger.get_account(s, user.id, "giro")
    cards = goals.cards(goals.list_goals(s, user.id), giro)
    active = [c for c in cards if not c["goal"].done_at]
    done = [c for c in reversed(cards) if c["goal"].done_at]
    return render(request, "goals.html", user=user, giro=giro, active=active, done=done,
                  can_add=len(active) < goals.MAX_ACTIVE_GOALS, emojis=GOAL_EMOJIS, error=error)


def _own_goal(s: Session, user: User, goal_id: int) -> Goal:
    g = s.get(Goal, goal_id)
    if not g or g.user_id != user.id:
        raise HTTPException(404)
    return g


@router.get("/ziele")
def goals_page(request: Request, user: User = Depends(kid), s: Session = db):
    return _goals_page(request, s, user)


@router.post("/ziele")
def goal_create(request: Request, name: str = Form(""), emoji: str = Form(""), cents: int = Form(0),
                photo: UploadFile | None = File(None), user: User = Depends(kid), s: Session = db,
                today: date = Depends(get_today)):
    data = photo.file.read(goals.MAX_PHOTO_BYTES + 1) if photo else b""  # bounded read, size is checked in the ledger
    try:
        goals.create_goal(s, user.id, name, emoji if emoji in GOAL_EMOJIS else GOAL_EMOJIS[0], cents, data or None, today)
    except LedgerError as e:
        return _goals_page(request, s, user, e.args[0])
    return redirect("/ziele")


@router.post("/ziele/{goal_id}/loeschen")
def goal_delete(goal_id: int, user: User = Depends(kid), s: Session = db):
    g = _own_goal(s, user, goal_id)
    if g.done_at:
        raise HTTPException(404)  # finished goals stay as a record
    s.delete(g)
    return redirect("/ziele")


@router.post("/ziele/{goal_id}/geschafft")
def goal_finish(request: Request, goal_id: int, user: User = Depends(kid), s: Session = db,
                today: date = Depends(get_today)):
    g = _own_goal(s, user, goal_id)
    try:
        goals.finish_goal(g, ledger.get_account(s, user.id, "giro"), today)
    except LedgerError as e:
        return _goals_page(request, s, user, e.args[0])
    return render(request, "done.html", user=user, emoji="✅", msg=t("goal.done.msg"), lesson=t("goal.done.hint"))


@router.get("/ziele/{goal_id}/bild")
def goal_photo(goal_id: int, user: User = Depends(current_user), s: Session = db):
    g = s.get(Goal, goal_id)
    if not g or not g.photo or (user.role != "parent" and g.user_id != user.id):
        raise HTTPException(404)
    return Response(g.photo, media_type="image/jpeg",
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"})
