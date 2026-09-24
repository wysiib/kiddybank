"""All money movement. Functions flush but never commit: the caller owns the transaction.

Errors are LedgerError(i18n_key), never sentences.
"""

import calendar
from datetime import date, datetime, time, timedelta

from sqlmodel import Session, select

from .auth import hash_pin
from .models import Account, RecurringRule, Transaction, User

MAX_CENTS = 100_000_000  # 1 million EUR: far above any pocket money, far below SQLite's 64-bit integers
INTEREST_DENOM = 10_000 * 365  # interest_accrued unit: 1 cent
PAYOUT_PERIODS = (7, 30, 365)  # a parent picks per kid how often interest is booked; rates are entered per that period
DEFAULT_PAYOUT_DAYS = 7


def annual_bp(period_bp: int, days: int) -> int:
    """'1 % per week' (100 bp, 7) -> annual basis points, the only unit that is stored."""
    return round(period_bp * 365 / days)


def period_bp(annual: int, days: int) -> int:
    return round(annual * days / 365)


# Defaults only, everything below is editable by a parent. Deliberately high for kids: 10 EUR must
# visibly earn something within a week.
DEFAULT_CHECKING_BP = annual_bp(100, 7)  # 1 % per week
MAX_RATE_BP = annual_bp(10_000, 7)  # 100 % per week


class LedgerError(Exception):
    """args[0] is an i18n key."""


def check_rate(rate_bp: int, days: int = DEFAULT_PAYOUT_DAYS) -> None:
    if not 0 <= rate_bp <= MAX_RATE_BP or days not in PAYOUT_PERIODS:
        raise LedgerError("err.rate")


def create_user(s: Session, name: str, role: str, pin: str, avatar: str = "🐷", today: date | None = None,
                checking_rate_bp: int = DEFAULT_CHECKING_BP, payout_days: int = DEFAULT_PAYOUT_DAYS) -> User:
    today = today or date.today()
    rate = checking_rate_bp if role == "child" else 0  # a parent's account is not part of the interest lesson
    check_rate(rate, payout_days)
    u = User(name=name, role=role, pin_hash=hash_pin(pin), avatar=avatar)
    s.add(u)
    s.flush()
    s.add(Account(user_id=u.id, type="checking", interest_rate_bp=rate, payout_days=payout_days,
                     last_updated=today, interest_paid_on=today))
    s.flush()
    return u


def set_checking_rate(s: Session, checking: Account, rate_bp: int, today: date, payout_days: int | None = None) -> None:
    payout_days = payout_days or checking.payout_days
    check_rate(rate_bp, payout_days)
    ensure_up_to_date(s, checking, today)  # settle interest at the old rate first
    checking.interest_rate_bp, checking.payout_days = rate_bp, payout_days


def kids(s: Session) -> list[User]:
    return list(s.exec(select(User).where(User.role == "child").order_by(User.id)).all())


def get_account(s: Session, user_id: int, type: str) -> Account:
    return s.exec(select(Account).where(Account.user_id == user_id, Account.type == type)).one()


def check_amount(cents: int) -> None:
    if not 0 < cents <= MAX_CENTS:
        raise LedgerError("err.amount")


def check_debit(acc: Account, cents: int) -> None:
    check_amount(cents)
    if acc.balance_cents < cents and not acc.allow_overdraft:
        raise LedgerError("err.insufficient")


def stamp(today: date) -> datetime:
    """Timestamp for "now" on the injected day. Everything time-stamped goes through here, so tests can move the clock."""
    return datetime.combine(today, datetime.now().time())


def post(s: Session, from_acc: Account | None, to_acc: Account | None, cents: int, type: str, today: date,
         note: str = "", seen: bool = False, now: datetime | None = None) -> Transaction:
    """Every booking. Settles interest and due allowances on both sides first, so the invariant
    'up to date before any balance change' cannot be forgotten. A None side is the virtual parent/bank/market."""
    for acc in (from_acc, to_acc):
        if acc is not None:
            ensure_up_to_date(s, acc, today)
    return _post(s, from_acc, to_acc, cents, type, now or stamp(today), note, seen)


def _post(s: Session, from_acc: Account | None, to_acc: Account | None, cents: int, type: str, now: datetime,
          note: str = "", seen: bool = False) -> Transaction:
    """The booking itself, without settling. Used by the settling code (interest, allowance) so it cannot recurse."""
    check_amount(cents)
    if from_acc is not None:
        check_debit(from_acc, cents)
        from_acc.balance_cents -= cents
    if to_acc is not None:
        to_acc.balance_cents += cents
    t = Transaction(from_account_id=from_acc and from_acc.id, to_account_id=to_acc and to_acc.id,
                    amount_cents=cents, type=type, timestamp=now, note=note, seen_at=now if seen else None)
    s.add(t)
    s.flush()
    return t


def transfer(s: Session, from_acc: Account, to_acc: Account, cents: int, today: date) -> Transaction:
    if from_acc.id == to_acc.id:
        raise LedgerError("err.same_account")
    if "term_deposit" in (from_acc.type, to_acc.type):
        raise LedgerError("err.term_deposit_locked")
    return post(s, from_acc, to_acc, cents, "manual", today)


def manual_booking(s: Session, acc: Account, cents: int, today: date, note: str = "") -> Transaction:
    """Parent booking: positive = deposit, negative = withdrawal."""
    if cents >= 0:
        return post(s, None, acc, cents, "manual", today, note)
    return post(s, acc, None, -cents, "manual", today, note)


# --- lazy catch-up -----------------------------------------------------------------------------

def _replay(acc: Account, rules, until: date) -> tuple[list[tuple[datetime, str, int]], dict]:
    """What a scheduler would have done from acc.last_updated to `until`, in date order: interest payouts (every
    payout_days from interest_paid_on) and allowance runs. Pure: returns the bookings and the end state, changes nothing.
    Rules are always paid by a parent (add_rule), so every allowance run happens."""
    balance, accrued, paid_on, last = acc.balance_cents, acc.interest_accrued, acc.interest_paid_on, acc.last_updated
    runs = {r.id: r.next_run for r in rules}
    bookings = []

    def accrue(day: date) -> None:
        nonlocal accrued, last
        if day > last:
            if balance > 0:
                accrued += balance * acc.interest_rate_bp * (day - last).days
            last = day

    while True:
        payout_day = paid_on + timedelta(days=acc.payout_days)
        rule = min(rules, key=lambda r: runs[r.id], default=None)
        if rule and runs[rule.id] < payout_day:  # on the same day the interest (00:00) comes before the allowance (08:00)
            day = runs[rule.id]
        else:
            day, rule = payout_day, None
        if day > until:
            break
        accrue(day)
        if rule:
            bookings.append((datetime.combine(day, time(8)), "recurring", rule.amount_cents))
            balance += rule.amount_cents
            runs[rule.id] = _next_run(day, rule.interval)
        else:
            cents = _round_cents(accrued)
            accrued -= cents * INTEREST_DENOM  # carry may be slightly negative after rounding up
            paid_on = day
            if cents:
                bookings.append((datetime.combine(day, time(0)), "interest", cents))
                balance += cents
    accrue(until)
    return bookings, {"accrued": accrued, "paid_on": paid_on, "last": last, "runs": runs}


def _rules(s: Session, acc: Account) -> list[RecurringRule]:
    return list(s.exec(select(RecurringRule).where(RecurringRule.to_account_id == acc.id)).all())


def ensure_up_to_date(s: Session, acc: Account, today: date) -> None:
    """Book what _replay computed since the last visit. Nothing can change a balance without passing here first,
    so the balance was constant in between and each period's interest is exact and compounds like the real thing."""
    if acc.type == "term_deposit":
        return
    rules = _rules(s, acc)
    bookings, st = _replay(acc, rules, today)
    for when, type, cents in bookings:
        _post(s, None, acc, cents, type, when)
    acc.interest_accrued, acc.interest_paid_on, acc.last_updated = st["accrued"], st["paid_on"], st["last"]
    for r in rules:
        r.next_run = st["runs"][r.id]


def projection(s: Session, acc: Account, today: date, days: int) -> tuple[int, int]:
    """(pocket money, interest) the account would get in the next `days` if nothing is spent. Call after catch-up."""
    bookings, _ = _replay(acc, _rules(s, acc), today + timedelta(days=days))
    return (sum(c for _, k, c in bookings if k == "recurring"), sum(c for _, k, c in bookings if k == "interest"))


def catch_up_user(s: Session, user_id: int, today: date) -> None:
    for acc in s.exec(select(Account).where(Account.user_id == user_id)).all():
        ensure_up_to_date(s, acc, today)


def _round_cents(units: int) -> int:
    """Nearest cent, not floor: '1 % per week' is stored as 5214 bp/year (5214.29 exactly) and must still pay 1,00 on 100."""
    return (units + INTEREST_DENOM // 2) // INTEREST_DENOM


def interest_cents(cents: int, rate_bp: int, days: int) -> int:
    return _round_cents(cents * rate_bp * days)


def next_interest(acc: Account, today: date) -> tuple[int, int]:
    """(days until the next payout, cents expected then if the balance stays as it is). Call after catch-up."""
    days = max((acc.interest_paid_on + timedelta(days=acc.payout_days) - today).days, 1)
    total = acc.interest_accrued + max(acc.balance_cents, 0) * acc.interest_rate_bp * days
    return days, _round_cents(total)


def _next_run(d: date, interval: str) -> date:
    if interval == "weekly":
        return d + timedelta(days=7)
    year, month = d.year + d.month // 12, d.month % 12 + 1
    # ponytail: a 31st clamps to the month end and then drifts (31 Jan -> 28 Feb -> 28 Mar); fine for pocket money
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _rule_anchor(interval: str, weekday: int, monthday: int) -> int:
    if interval not in ("weekly", "monthly") or not (0 <= weekday <= 6 and 1 <= monthday <= 28):
        raise LedgerError("err.amount")
    return weekday if interval == "weekly" else monthday


def add_rule(s: Session, checking: Account, cents: int, interval: str, weekday: int, monthday: int, today: date) -> RecurringRule:
    """Pocket money from the virtual parent into `checking`, first paid on the next chosen weekday / day of the month."""
    check_amount(cents)
    anchor = _rule_anchor(interval, weekday, monthday)
    r = RecurringRule(from_account_id=None, to_account_id=checking.id, amount_cents=cents, interval=interval,
                      next_run=first_run(interval, anchor, today))
    s.add(r)
    s.flush()
    return r


def update_rule(s: Session, r: RecurringRule, cents: int, interval: str, weekday: int, monthday: int, today: date) -> None:
    check_amount(cents)
    anchor = _rule_anchor(interval, weekday, monthday)
    ensure_up_to_date(s, s.get(Account, r.to_account_id), today)  # pay out anything due under the old schedule first
    r.amount_cents, r.interval, r.next_run = cents, interval, first_run(interval, anchor, today)


def first_run(interval: str, anchor: int, today: date) -> date:
    """First payday strictly after today: weekday 0-6 (Mon-Sun) for weekly, day of month 1-28 for monthly.
    Monthly caps at 28 so every month has the day and _next_run never drifts."""
    if interval == "weekly":
        return today + timedelta(days=(anchor - today.weekday()) % 7 or 7)
    d = today.replace(day=anchor)
    return d if d > today else _next_run(d, "monthly")


# --- reading -----------------------------------------------------------------------------------

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


def week_summary(s: Session, checking: Account, today: date) -> dict[str, int]:
    """Cents in / out of the checking account over the last 7 days (today + 6), for the home card.
    Term deposit moves are saving, not income or spending, so they are left out."""
    since = datetime.combine(today - timedelta(days=6), time(0))  # no upper bound: nothing is stamped after today
    out = {"recurring": 0, "interest": 0, "other": 0, "spent": 0}
    for tx in s.exec(select(Transaction).where(
            (Transaction.from_account_id == checking.id) | (Transaction.to_account_id == checking.id),
            Transaction.timestamp >= since, Transaction.type != "term_deposit")).all():
        if tx.to_account_id == checking.id:
            out[tx.type if tx.type in ("interest", "recurring") else "other"] += tx.amount_cents
        else:
            out["spent"] += tx.amount_cents
    return out


