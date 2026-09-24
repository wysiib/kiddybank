"""Term deposits (the kid's "treasure chest"): parent-managed offers, deposits, payout and collecting."""

from datetime import date, timedelta

from sqlmodel import Session, select

from . import coins, ledger
from .i18n import t
from .models import Account, TermDepositProduct

DEFAULT_PRODUCTS = (("td.seed.short", 7, ledger.annual_bp(150, 7)), ("td.seed.medium", 14, ledger.annual_bp(200, 7)),
                    ("td.seed.long", 30, ledger.annual_bp(300, 7)))  # name key, term days, rate bp; entered per week


def _check_product(name: str, term_days: int, rate_bp: int, rate_days: int) -> None:
    if not name.strip():
        raise ledger.LedgerError("err.name")
    if not 1 <= term_days <= 3650:
        raise ledger.LedgerError("err.term")
    ledger.check_rate(rate_bp, rate_days)


def add_product(s: Session, name: str, term_days: int, rate_bp: int, rate_days: int = 365) -> TermDepositProduct:
    _check_product(name, term_days, rate_bp, rate_days)
    p = TermDepositProduct(name=name.strip(), term_days=term_days, rate_bp=rate_bp, rate_days=rate_days)
    s.add(p)
    s.flush()
    return p


def seed_default_products(s: Session) -> None:
    if not s.exec(select(TermDepositProduct)).first():
        for name, days, bp in DEFAULT_PRODUCTS:
            add_product(s, t(name), days, bp, rate_days=ledger.DEFAULT_PAYOUT_DAYS)


def open_term_deposit(s: Session, checking: Account, cents: int, product: TermDepositProduct, today: date) -> Account:
    ledger.ensure_up_to_date(s, checking, today)
    ledger.check_debit(checking, cents)
    td = Account(user_id=checking.user_id, type="term_deposit", name=product.name, interest_rate_bp=product.rate_bp,
                 last_updated=today, interest_paid_on=today, opened_at=today,
                 maturity_date=today + timedelta(days=product.term_days))
    s.add(td)
    s.flush()
    ledger.post(s, checking, td, cents, "term_deposit", today)
    return td


def term_deposit_status(td: Account, today: date) -> str:
    if td.collected_at:
        return "collected"
    return "ready" if today >= td.maturity_date else "locked"


def payout(cents: int, rate_bp: int, term_days: int) -> tuple[int, int]:
    """(total, interest) of a term deposit held to maturity. Feeds the preview, the deposit card and the booking."""
    interest = ledger.interest_cents(cents, rate_bp, term_days)
    return cents + interest, interest


def term_deposit_payout(td: Account) -> tuple[int, int]:
    return payout(td.balance_cents, td.interest_rate_bp, (td.maturity_date - td.opened_at).days)


STACK_DEMO_CENTS = 1000  # amount the offer stacks show until the kid has dialled one in


def offer_stacks(s: Session, checking: Account, cents: int):
    """The offers plus what to draw for each: this checking account's and the offer's interest on `cents` as coins, and the
    term as calendars. Interest coins share one unit and terms share one calendar unit, so the offers compare."""
    products = s.exec(select(TermDepositProduct).order_by(TermDepositProduct.term_days)).all()
    cents = cents if cents > 0 else STACK_DEMO_CENTS
    bonus = {p.id: (ledger.interest_cents(cents, checking.interest_rate_bp, p.term_days), ledger.interest_cents(cents, p.rate_bp, p.term_days))
             for p in products}
    unit = coins.coin_unit([b for pair in bonus.values() for b in pair], coins.SMALL_LADDER, coins.SMALL_CAP)
    cal = coins.time_unit(max((p.term_days for p in products), default=1))
    by_id = {p.id: {"checking": g, "td": f, "checking_coins": coins.coins(g, unit), "td_coins": coins.coins(f, unit),
                    "cals": coins.time_units(p.term_days, cal)}
             for p in products for g, f in [bonus[p.id]]}
    return products, {"unit": unit, "cal": cal, "by_id": by_id}


def collect_term_deposit(s: Session, td: Account, today: date) -> tuple[int, int]:
    """Pays the deposit plus interest into the checking account. Returns (total, interest)."""
    status = term_deposit_status(td, today)
    if status != "ready":
        raise ledger.LedgerError("err.term_deposit_locked" if status == "locked" else "err.already_collected")
    total, interest = term_deposit_payout(td)
    checking = ledger.get_account(s, td.user_id, "checking")
    if interest:
        ledger.post(s, None, td, interest, "interest", today, seen=True)  # the kid is looking at it right now
    ledger.post(s, td, checking, total, "term_deposit", today, seen=True)
    td.collected_at = ledger.stamp(today)
    return total, interest


