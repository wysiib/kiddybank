"""All money movement. Functions flush but never commit: the caller owns the transaction.

Errors are LedgerError(i18n_key), never sentences.
"""

import calendar
from datetime import date, datetime, time, timedelta

from sqlmodel import Session, select

from .auth import hash_pin
from .models import Account, FestgeldProduct, Goal, RecurringRule, Transaction, User

INTEREST_DENOM = 10_000 * 365  # interest_accrued unit: 1 cent
INTEREST_PAYOUT_DAYS = 7  # interest is booked at most weekly, so celebrations stay special
# Defaults only, everything below is editable by a parent. Deliberately high for kids: 10 EUR must
# visibly earn something within weeks.
DEFAULT_GIRO_BP = 200
DEFAULT_PRODUCTS = (("Kurz", 7, 1200), ("Mittel", 14, 1500), ("Lang", 30, 2000))  # name, term days, rate bp
MAX_RATE_BP = 10_000  # 100 % per year


class LedgerError(Exception):
    """args[0] is an i18n key."""


def _check_rate(rate_bp: int) -> None:
    if not 0 <= rate_bp <= MAX_RATE_BP:
        raise LedgerError("err.rate")


def create_user(s: Session, name: str, role: str, pin: str, avatar: str = "🐷", today: date | None = None,
                giro_rate_bp: int = DEFAULT_GIRO_BP) -> User:
    today = today or date.today()
    rate = giro_rate_bp if role == "child" else 0  # a parent's account is not part of the interest lesson
    _check_rate(rate)
    u = User(name=name, role=role, pin_hash=hash_pin(pin), avatar=avatar)
    s.add(u)
    s.flush()
    s.add(Account(user_id=u.id, type="giro", interest_rate_bp=rate, last_updated=today, interest_paid_on=today))
    s.flush()
    return u


def set_giro_rate(s: Session, giro: Account, rate_bp: int, today: date) -> None:
    _check_rate(rate_bp)
    ensure_up_to_date(s, giro, today)  # settle interest at the old rate first
    giro.interest_rate_bp = rate_bp


def get_account(s: Session, user_id: int, type: str) -> Account:
    return s.exec(select(Account).where(Account.user_id == user_id, Account.type == type)).one()


def _check_debit(acc: Account, cents: int) -> None:
    if cents <= 0:
        raise LedgerError("err.amount")
    if acc.balance_cents < cents and not acc.allow_overdraft:
        raise LedgerError("err.insufficient")


def post(s: Session, from_acc: Account | None, to_acc: Account | None, cents: int, type: str,
         note: str = "", now: datetime | None = None, seen: bool = False) -> Transaction:
    """Low level: one atomic booking. A None side is the virtual parent/bank/market."""
    if from_acc is not None:
        _check_debit(from_acc, cents)
        from_acc.balance_cents -= cents
    elif cents <= 0:
        raise LedgerError("err.amount")
    if to_acc is not None:
        to_acc.balance_cents += cents
    now = now or datetime.now()
    t = Transaction(from_account_id=from_acc and from_acc.id, to_account_id=to_acc and to_acc.id,
                    amount_cents=cents, type=type, timestamp=now, note=note, seen_at=now if seen else None)
    s.add(t)
    s.flush()
    return t


def transfer(s: Session, from_acc: Account, to_acc: Account, cents: int, today: date, note: str = "") -> Transaction:
    if from_acc.id == to_acc.id:
        raise LedgerError("err.same_account")
    if "festgeld" in (from_acc.type, to_acc.type):
        raise LedgerError("err.festgeld_locked")
    ensure_up_to_date(s, from_acc, today)
    ensure_up_to_date(s, to_acc, today)
    return post(s, from_acc, to_acc, cents, "manual", note)


def manual_booking(s: Session, acc: Account, cents: int, today: date, note: str = "") -> Transaction:
    """Parent booking: positive = deposit, negative = withdrawal."""
    ensure_up_to_date(s, acc, today)
    if cents >= 0:
        return post(s, None, acc, cents, "manual", note)
    return post(s, acc, None, -cents, "manual", note)


# --- lazy catch-up -----------------------------------------------------------------------------

def ensure_up_to_date(s: Session, acc: Account, today: date) -> None:
    """Must run before ANY balance change, so interest is computed on the balance that actually held."""
    if acc.type == "festgeld":
        return
    accrue_interest(s, acc, today)
    run_recurring(s, acc, today)


def catch_up_user(s: Session, user_id: int, today: date) -> None:
    for acc in s.exec(select(Account).where(Account.user_id == user_id)).all():
        ensure_up_to_date(s, acc, today)


def accrue_interest(s: Session, acc: Account, today: date) -> None:
    days = (today - acc.last_updated).days
    if days <= 0:
        return
    if acc.balance_cents > 0:
        acc.interest_accrued += acc.balance_cents * acc.interest_rate_bp * days
    acc.last_updated = today
    if (today - acc.interest_paid_on).days >= INTEREST_PAYOUT_DAYS:
        cents, acc.interest_accrued = divmod(acc.interest_accrued, INTEREST_DENOM)
        acc.interest_paid_on = today
        if cents:
            post(s, None, acc, cents, "zins", now=datetime.combine(today, time(0)))


def _next_run(d: date, interval: str) -> date:
    if interval == "weekly":
        return d + timedelta(days=7)
    year, month = d.year + d.month // 12, d.month % 12 + 1
    # ponytail: a 31st clamps to the month end and then drifts (31 Jan -> 28 Feb -> 28 Mar); fine for pocket money
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def first_run(interval: str, anchor: int, today: date) -> date:
    """First payday strictly after today: weekday 0-6 (Mon-Sun) for weekly, day of month 1-28 for monthly.
    Monthly caps at 28 so every month has the day and _next_run never drifts."""
    if interval == "weekly":
        return today + timedelta(days=(anchor - today.weekday()) % 7 or 7)
    d = today.replace(day=anchor)
    return d if d > today else _next_run(d, "monthly")


def run_recurring(s: Session, acc: Account, today: date) -> None:
    rules = s.exec(select(RecurringRule).where(RecurringRule.to_account_id == acc.id,
                                               RecurringRule.next_run <= today)).all()
    for r in rules:
        src = s.get(Account, r.from_account_id) if r.from_account_id else None
        while r.next_run <= today:
            try:
                post(s, src, acc, r.amount_cents, "dauerauftrag", now=datetime.combine(r.next_run, time(8)))
            except LedgerError:
                pass  # ponytail: payer lacks funds, that occurrence is skipped
            r.next_run = _next_run(r.next_run, r.interval)


# --- festgeld ----------------------------------------------------------------------------------

def _check_product(name: str, term_days: int, rate_bp: int) -> None:
    if not name.strip():
        raise LedgerError("err.name")
    if not 1 <= term_days <= 3650:
        raise LedgerError("err.term")
    _check_rate(rate_bp)


def add_product(s: Session, name: str, term_days: int, rate_bp: int) -> FestgeldProduct:
    _check_product(name, term_days, rate_bp)
    p = FestgeldProduct(name=name.strip(), term_days=term_days, rate_bp=rate_bp)
    s.add(p)
    s.flush()
    return p


def update_product(p: FestgeldProduct, name: str, term_days: int, rate_bp: int, active: bool) -> None:
    """Only affects deposits opened afterwards: each deposit snapshots name, rate and maturity."""
    _check_product(name, term_days, rate_bp)
    p.name, p.term_days, p.rate_bp, p.active = name.strip(), term_days, rate_bp, active


def seed_default_products(s: Session) -> None:
    if not s.exec(select(FestgeldProduct)).first():
        for name, days, bp in DEFAULT_PRODUCTS:
            add_product(s, name, days, bp)


def open_festgeld(s: Session, giro: Account, cents: int, product: FestgeldProduct, today: date) -> Account:
    if not product.active:
        raise LedgerError("err.term")
    ensure_up_to_date(s, giro, today)
    _check_debit(giro, cents)
    fg = Account(user_id=giro.user_id, type="festgeld", name=product.name, interest_rate_bp=product.rate_bp,
                 last_updated=today, interest_paid_on=today, opened_at=today,
                 maturity_date=today + timedelta(days=product.term_days))
    s.add(fg)
    s.flush()
    post(s, giro, fg, cents, "festgeld")
    return fg


def festgeld_status(fg: Account, today: date) -> str:
    if fg.collected_at:
        return "collected"
    return "ready" if today >= fg.maturity_date else "locked"


def festgeld_payout(fg: Account) -> tuple[int, int]:
    """(total, interest) if collected at maturity. Same function feeds the preview and the booking."""
    term = (fg.maturity_date - fg.opened_at).days
    interest = fg.balance_cents * fg.interest_rate_bp * term // INTEREST_DENOM
    return fg.balance_cents + interest, interest


def collect_festgeld(s: Session, fg: Account, today: date) -> int:
    status = festgeld_status(fg, today)
    if status != "ready":
        raise LedgerError("err.festgeld_locked" if status == "locked" else "err.already_collected")
    total, interest = festgeld_payout(fg)
    giro = get_account(s, fg.user_id, "giro")
    if interest:
        post(s, None, fg, interest, "zins", seen=True)  # the kid is looking at it right now
    post(s, fg, giro, total, "festgeld", seen=True)
    fg.collected_at = datetime.now()
    return total


# --- reading -----------------------------------------------------------------------------------

def unseen_events(s: Session, user_id: int) -> list[Transaction]:
    ids = [a.id for a in s.exec(select(Account).where(Account.user_id == user_id)).all()]
    return list(s.exec(select(Transaction).where(
        Transaction.to_account_id.in_(ids), Transaction.type.in_(("zins", "dauerauftrag")),
        Transaction.seen_at.is_(None)).order_by(Transaction.timestamp)).all())


def mark_seen(s: Session, user_id: int) -> None:
    now = datetime.now()
    for t in unseen_events(s, user_id):
        t.seen_at = now
    for g in unseen_reached_goals(s, user_id):
        g.reached_seen_at = now


def statement(s: Session, acc: Account, limit: int = 50) -> list[tuple[Transaction, int, int]]:
    """Newest first: (transaction, signed amount for this account, balance afterwards). Balance is derived."""
    txs = s.exec(select(Transaction).where(
        (Transaction.from_account_id == acc.id) | (Transaction.to_account_id == acc.id)
    ).order_by(Transaction.timestamp.desc(), Transaction.id.desc()).limit(limit)).all()
    rows, bal = [], acc.balance_cents
    for t in txs:
        delta = t.amount_cents if t.to_account_id == acc.id else -t.amount_cents
        rows.append((t, delta, bal))
        bal -= delta
    return rows


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
