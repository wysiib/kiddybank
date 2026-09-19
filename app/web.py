"""Shared web plumbing: settings, database session, templates, sign-in and the kid/parent dependencies."""

import os
import secrets
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from . import ledger
from .i18n import LOCALE, format_date, format_money, format_percent, format_plain, t
from .ledger import LedgerError
from .models import Account, User, make_engine

BASE = Path(__file__).parent
DB_URL = os.environ.get("KIDDYBANK_DB", "sqlite:///kiddybank.db")
PARENT_IDLE = 15 * 60  # seconds; kids stay signed in for the cookie's 30 days, parents on a shared tablet must not

templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["t"] = t
templates.env.globals["locale"] = LOCALE
templates.env.filters["money"] = format_money
templates.env.filters["plain"] = format_plain
templates.env.filters["date"] = format_date
templates.env.filters["pct"] = format_percent
templates.env.filters["per"] = lambda bp, days: format_percent(ledger.period_bp(bp, days))  # annual bp -> "per week/month/year"


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


# scope="function": commit before the response goes out, so a failed commit is an error page, not a lost write behind a 200
db = Depends(get_session, scope="function")


def get_today() -> date:
    return date.today()


def render(request: Request, name: str, status_code: int | None = None, **ctx):
    """A page rendered with an `error` key is a 400 unless the caller says otherwise."""
    return templates.TemplateResponse(request, name, ctx, status_code=status_code or (400 if ctx.get("error") else 200))


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


class Redirect(Exception):
    """Raised by dependencies that send the user elsewhere (login, parent area)."""

    def __init__(self, url: str):
        self.url = url


def sign_in(request: Request, user: User) -> None:
    request.session.clear()
    request.session["uid"] = user.id
    request.session["seen"] = time.time()


def current_user(request: Request, s: Session = db) -> User:
    user = s.get(User, request.session.get("uid") or 0)
    if user and user.role == "parent" and time.time() - request.session.get("seen", 0) > PARENT_IDLE:
        request.session.clear()
        user = None
    if not user:
        raise Redirect("/login")
    request.session["seen"] = time.time()
    return user


def kid(user: User = Depends(current_user), s: Session = db, today: date = Depends(get_today)) -> User:
    if user.role != "child":
        raise Redirect("/eltern")
    ledger.catch_up_user(s, user.id, today)  # lazy interest / allowance, before anything else happens
    return user


def parent(user: User = Depends(current_user), s: Session = db, today: date = Depends(get_today)) -> User:
    if user.role != "parent":
        raise HTTPException(403, "err.forbidden")
    for k in ledger.kids(s):
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
        raise HTTPException(403, "err.module_off")


def own_accounts(s: Session, user: User, *types: str) -> list[Account]:
    q = select(Account).where(Account.user_id == user.id)
    if types:
        q = q.where(Account.type.in_(types))  # type: ignore[attr-defined]
    return list(s.exec(q.order_by(Account.id)).all())


def active_deposits(s: Session, user: User) -> list[Account]:
    return [a for a in own_accounts(s, user, "festgeld") if not a.collected_at]


def child_or_404(s: Session, uid: int) -> User:
    k = s.get(User, uid)
    if not k or k.role != "child":
        raise HTTPException(404)
    return k


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
