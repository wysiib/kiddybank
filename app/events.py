"""What the kid has not seen yet: interest, allowance and reached goals for the celebration screen."""

from datetime import date

from sqlmodel import Session, select

from . import goals, ledger
from .models import Account, Transaction

def unseen_events(s: Session, user_id: int) -> list[Transaction]:
    ids = [a.id for a in s.exec(select(Account).where(Account.user_id == user_id)).all()]
    return list(s.exec(select(Transaction).where(
        Transaction.to_account_id.in_(ids), Transaction.type.in_(("zins", "dauerauftrag")),
        Transaction.seen_at.is_(None)).order_by(Transaction.timestamp)).all())


def mark_seen(s: Session, user_id: int, today: date) -> None:
    now = ledger.stamp(today)
    for t in unseen_events(s, user_id):
        t.seen_at = now
    for g in goals.unseen_reached_goals(s, user_id):
        g.reached_seen_at = now
