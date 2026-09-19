"""Sparziele: progress-only savings goals, nothing is reserved and nothing is booked."""

from datetime import date

from sqlalchemy.orm import defer
from sqlmodel import Session, select

from . import ledger
from .models import Account, Goal

MAX_ACTIVE_GOALS = 3
MAX_PHOTO_BYTES = 300_000  # the browser shrinks photos to ~50 KB, this only stops abuse


def list_goals(s: Session, user_id: int) -> list[Goal]:
    # finished goals are kept forever, so their photos stay out of every page load; only /ziele/{id}/bild reads one
    return list(s.exec(select(Goal).where(Goal.user_id == user_id).options(defer(Goal.photo)).order_by(Goal.id)).all())


def goal_progress(goal: Goal, giro: Account) -> int:
    """Whole percent (0-100) of the target the Giro balance covers."""
    if goal.done_at:
        return 100
    return max(0, min(100, giro.balance_cents * 100 // goal.target_cents))


def goal_reached(goal: Goal, giro: Account) -> bool:
    return goal.done_at is None and giro.balance_cents >= goal.target_cents


def create_goal(s: Session, user_id: int, name: str, emoji: str, target_cents: int, photo: bytes | None,
                today: date) -> Goal:
    name = name.strip()[:40]
    ledger.check_amount(target_cents)
    if photo is not None:
        if len(photo) > MAX_PHOTO_BYTES:
            raise ledger.LedgerError("err.photo_size")
        # ponytail: JPEG only (the browser re-encodes everything to JPEG); add Pillow if other formats are ever needed
        if not photo.startswith(b"\xff\xd8\xff"):
            raise ledger.LedgerError("err.photo_type")
    if not name and photo is None:
        raise ledger.LedgerError("err.goal_empty")
    if sum(1 for g in list_goals(s, user_id) if g.done_at is None) >= MAX_ACTIVE_GOALS:
        raise ledger.LedgerError("err.goal_limit")
    now = ledger.stamp(today)
    goal = Goal(user_id=user_id, name=name, emoji=emoji, target_cents=target_cents, photo=photo,
                has_photo=photo is not None, created_at=now)
    if goal_reached(goal, ledger.get_account(s, user_id, "giro")):
        goal.reached_seen_at = now  # already affordable at creation: no fake celebration
    s.add(goal)
    s.flush()
    return goal


def finish_goal(goal: Goal, giro: Account, today: date) -> None:
    if not goal_reached(goal, giro):
        raise ledger.LedgerError("err.goal_not_reached")
    goal.done_at = ledger.stamp(today)


def unseen_reached_goals(s: Session, user_id: int) -> list[Goal]:
    giro = ledger.get_account(s, user_id, "giro")
    return [g for g in list_goals(s, user_id) if g.reached_seen_at is None and goal_reached(g, giro)]
