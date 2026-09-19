"""Festgeld (the kid's "Schatztruhe"): parent-managed offers, deposits, payout and collecting."""

from datetime import date, timedelta

from sqlmodel import Session, select

from . import ledger
from .models import Account, FestgeldProduct

DEFAULT_PRODUCTS = (("Kurz", 7, ledger.annual_bp(150, 7)), ("Mittel", 14, ledger.annual_bp(200, 7)),
                    ("Lang", 30, ledger.annual_bp(300, 7)))  # name, term days, rate bp; entered per week


def _check_product(name: str, term_days: int, rate_bp: int, rate_days: int) -> None:
    if not name.strip():
        raise ledger.LedgerError("err.name")
    if not 1 <= term_days <= 3650:
        raise ledger.LedgerError("err.term")
    ledger.check_rate(rate_bp, rate_days)


def add_product(s: Session, name: str, term_days: int, rate_bp: int, rate_days: int = 365) -> FestgeldProduct:
    _check_product(name, term_days, rate_bp, rate_days)
    p = FestgeldProduct(name=name.strip(), term_days=term_days, rate_bp=rate_bp, rate_days=rate_days)
    s.add(p)
    s.flush()
    return p


def seed_default_products(s: Session) -> None:
    if not s.exec(select(FestgeldProduct)).first():
        for name, days, bp in DEFAULT_PRODUCTS:
            add_product(s, name, days, bp, rate_days=ledger.DEFAULT_PAYOUT_DAYS)


def open_festgeld(s: Session, giro: Account, cents: int, product: FestgeldProduct, today: date) -> Account:
    ledger.ensure_up_to_date(s, giro, today)
    ledger.check_debit(giro, cents)
    fg = Account(user_id=giro.user_id, type="festgeld", name=product.name, interest_rate_bp=product.rate_bp,
                 last_updated=today, interest_paid_on=today, opened_at=today,
                 maturity_date=today + timedelta(days=product.term_days))
    s.add(fg)
    s.flush()
    ledger.post(s, giro, fg, cents, "festgeld", today)
    return fg


def festgeld_status(fg: Account, today: date) -> str:
    if fg.collected_at:
        return "collected"
    return "ready" if today >= fg.maturity_date else "locked"


def payout(cents: int, rate_bp: int, term_days: int) -> tuple[int, int]:
    """(total, interest) of a Festgeld held to maturity. Feeds the preview, the deposit card and the booking."""
    interest = ledger.interest_cents(cents, rate_bp, term_days)
    return cents + interest, interest


def festgeld_payout(fg: Account) -> tuple[int, int]:
    return payout(fg.balance_cents, fg.interest_rate_bp, (fg.maturity_date - fg.opened_at).days)


TOWER_DEMO_CENTS = 1000  # amount the offer bars show until the kid has dialled one in


def offer_towers(s: Session, giro: Account, cents: int):
    """The offers plus, per offer, the bonus this Giro vs. that offer would pay on `cents` and the bar heights (0-100, one shared scale)."""
    products = s.exec(select(FestgeldProduct).order_by(FestgeldProduct.term_days)).all()
    cents = cents if cents > 0 else TOWER_DEMO_CENTS
    bonus = {p.id: (ledger.interest_cents(cents, giro.interest_rate_bp, p.term_days), ledger.interest_cents(cents, p.rate_bp, p.term_days))
             for p in products}
    top = max((b for pair in bonus.values() for b in pair), default=0)
    height = lambda b: max(8, b * 100 // top) if b else 0  # noqa: E731  a tiny bonus still gets a visible stub
    return products, {pid: {"giro": g, "fg": f, "giro_h": height(g), "fg_h": height(f)} for pid, (g, f) in bonus.items()}


def collect_festgeld(s: Session, fg: Account, today: date) -> tuple[int, int]:
    """Pays the deposit plus interest into the Giro. Returns (total, interest)."""
    status = festgeld_status(fg, today)
    if status != "ready":
        raise ledger.LedgerError("err.festgeld_locked" if status == "locked" else "err.already_collected")
    total, interest = festgeld_payout(fg)
    giro = ledger.get_account(s, fg.user_id, "giro")
    if interest:
        ledger.post(s, None, fg, interest, "zins", today, seen=True)  # the kid is looking at it right now
    ledger.post(s, fg, giro, total, "festgeld", today, seen=True)
    fg.collected_at = ledger.stamp(today)
    return total, interest


