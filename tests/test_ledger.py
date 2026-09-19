from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import ledger, market
from app.ledger import LedgerError
from app.models import FestgeldProduct, Goal, PriceHistory, RecurringRule, Transaction, make_engine

D0 = date(2026, 1, 1)


def new_session(tmp_path, name="t.db"):
    return Session(make_engine(f"sqlite:///{tmp_path}/{name}"))


@pytest.fixture
def s(tmp_path):
    with new_session(tmp_path) as session:
        yield session


@pytest.fixture
def kids(s):
    mia = ledger.create_user(s, "Mia", "child", "1234", today=D0, giro_rate_bp=1000)  # 10 %/year
    tom = ledger.create_user(s, "Tom", "child", "4321", today=D0, giro_rate_bp=1000)
    return mia, tom


@pytest.fixture
def kurz(s):
    return ledger.add_product(s, "Kurz", 7, 1200)


def giro(s, user):
    return ledger.get_account(s, user.id, "giro")


def fund(s, acc, cents):
    ledger.manual_booking(s, acc, cents, D0)


def test_transfer_moves_money_and_rejects_overdraft(s, kids):
    mia, tom = kids
    fund(s, giro(s, mia), 1000)
    ledger.transfer(s, giro(s, mia), giro(s, tom), 400, D0)
    assert (giro(s, mia).balance_cents, giro(s, tom).balance_cents) == (600, 400)

    with pytest.raises(LedgerError, match="err.insufficient"):
        ledger.transfer(s, giro(s, mia), giro(s, tom), 601, D0)
    assert (giro(s, mia).balance_cents, giro(s, tom).balance_cents) == (600, 400)  # nothing moved

    with pytest.raises(LedgerError, match="err.amount"):
        ledger.transfer(s, giro(s, mia), giro(s, tom), 0, D0)


def test_db_check_blocks_negative_balance(s, kids):
    giro(s, kids[0]).balance_cents = -1
    with pytest.raises(IntegrityError):
        s.flush()


def test_interest_is_weekly_exact_and_idempotent(s, kids):
    sp = giro(s, kids[0])
    sp.balance_cents = 10_000  # 100 EUR at 10 %/year
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=3))
    assert sp.balance_cents == 10_000  # not a payout day yet

    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=30))
    assert sp.balance_cents == 10_000 + 82  # 10000 * 10% * 30/365 = 82.19 cents
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=30))
    assert sp.balance_cents == 10_082  # catching up twice books once
    assert s.exec(select(Transaction).where(Transaction.type == "zins")).all().__len__() == 1


def test_next_interest_predicts_the_weekly_payout(s, kids):
    sp = giro(s, kids[0])
    sp.balance_cents = 10_000  # 100 EUR at 10 %/year = 19.17 cents/week
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=3))
    days, cents = ledger.next_interest(sp, D0 + timedelta(days=3))
    assert (days, cents) == (4, 19)  # a week's interest, booked on day 7
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=7))
    assert sp.balance_cents == 10_000 + cents


def test_interest_remainder_is_carried_not_lost(s, kids):
    sp = giro(s, kids[0])
    sp.balance_cents = 10_000
    for week in range(1, 5):  # weekly visits: 19.17 cents/week, remainders must add up
        ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=7 * week))
    assert sp.balance_cents - 10_000 in (76, 77)  # 28 days = 76.7 cents


def test_recurring_allowance_catches_up_once(s, kids):
    mia, _ = kids
    g = giro(s, mia)
    s.add(RecurringRule(from_account_id=None, to_account_id=g.id, amount_cents=500, interval="weekly",
                        next_run=D0 + timedelta(days=7)))
    s.flush()
    ledger.ensure_up_to_date(s, g, D0 + timedelta(days=21))
    assert g.balance_cents == 1500
    ledger.ensure_up_to_date(s, g, D0 + timedelta(days=21))
    assert g.balance_cents == 1500


def test_monthly_rule_clamps_to_month_end():
    assert ledger._next_run(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert ledger._next_run(date(2026, 12, 15), "monthly") == date(2027, 1, 15)


def test_festgeld_lock_collect_and_payout(s, kids, kurz):
    mia, _ = kids
    fund(s, giro(s, mia), 10_000)
    fg = ledger.open_festgeld(s, giro(s, mia), 5_000, kurz, D0)
    assert giro(s, mia).balance_cents == 5_000 and fg.balance_cents == 5_000
    assert ledger.festgeld_status(fg, D0) == "locked"

    with pytest.raises(LedgerError, match="err.festgeld_locked"):
        ledger.collect_festgeld(s, fg, D0 + timedelta(days=6))
    with pytest.raises(LedgerError, match="err.festgeld_locked"):  # no other way out either
        ledger.transfer(s, fg, giro(s, mia), 100, D0 + timedelta(days=8))

    maturity = D0 + timedelta(days=7)
    assert ledger.festgeld_status(fg, maturity) == "ready"
    total, interest = ledger.festgeld_payout(fg)
    assert interest == 11  # 5000 * 12% * 7/365 = 11.5 cents, rounded down
    assert ledger.collect_festgeld(s, fg, maturity) == total == 5_011
    assert giro(s, mia).balance_cents == 5_000 + 5_011 and fg.balance_cents == 0
    assert ledger.festgeld_status(fg, maturity) == "collected"

    with pytest.raises(LedgerError, match="err.already_collected"):
        ledger.collect_festgeld(s, fg, maturity)


def test_several_deposits_at_once_keep_their_own_rate(s, kids, kurz):
    mia, _ = kids
    lang = ledger.add_product(s, "Lang", 30, 2000)
    fund(s, giro(s, mia), 10_000)
    a = ledger.open_festgeld(s, giro(s, mia), 3_000, kurz, D0)
    b = ledger.open_festgeld(s, giro(s, mia), 4_000, lang, D0)
    assert giro(s, mia).balance_cents == 3_000
    assert (a.name, a.interest_rate_bp, b.name, b.interest_rate_bp) == ("Kurz", 1200, "Lang", 2000)

    ledger.update_product(lang, "Lang", 30, 500, True)  # a later rate change must not touch open deposits
    assert b.interest_rate_bp == 2000
    assert ledger.festgeld_payout(b)[1] == 4_000 * 2000 * 30 // ledger.INTEREST_DENOM

    ledger.collect_festgeld(s, a, D0 + timedelta(days=7))  # collecting one leaves the other locked
    assert ledger.festgeld_status(b, D0 + timedelta(days=7)) == "locked"


def test_inactive_product_cannot_be_opened(s, kids, kurz):
    mia, _ = kids
    fund(s, giro(s, mia), 1_000)
    ledger.update_product(kurz, "Kurz", 7, 1200, active=False)
    with pytest.raises(LedgerError, match="err.term"):
        ledger.open_festgeld(s, giro(s, mia), 500, kurz, D0)
    assert giro(s, mia).balance_cents == 1_000


def test_rate_change_settles_interest_at_the_old_rate_first(s, kids):
    g = giro(s, kids[0])
    g.balance_cents = 10_000
    ledger.set_giro_rate(s, g, 0, D0 + timedelta(days=14))
    assert g.balance_cents == 10_000 + 38  # 10000 * 10% * 14/365 = 38.4 cents, at the old 10 %
    ledger.ensure_up_to_date(s, g, D0 + timedelta(days=60))
    assert g.balance_cents == 10_038  # new rate 0: nothing more


def test_rates_and_products_are_validated(s, kids):
    for bad in (-1, 10_001):
        with pytest.raises(LedgerError, match="err.rate"):
            ledger.set_giro_rate(s, giro(s, kids[0]), bad, D0)
        with pytest.raises(LedgerError, match="err.rate"):
            ledger.add_product(s, "X", 7, bad)
    with pytest.raises(LedgerError, match="err.term"):
        ledger.add_product(s, "X", 0, 100)
    with pytest.raises(LedgerError, match="err.name"):
        ledger.add_product(s, "  ", 7, 100)


def test_default_products_are_seeded_once(s):
    ledger.seed_default_products(s)
    ledger.seed_default_products(s)
    assert [(p.name, p.term_days, p.rate_bp) for p in s.exec(select(FestgeldProduct).order_by(FestgeldProduct.id))] == \
        list(ledger.DEFAULT_PRODUCTS)


def test_celebrations_show_once(s, kids):
    mia, _ = kids
    sp = giro(s, mia)
    sp.balance_cents = 10_000
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=30))
    assert [t.type for t in ledger.unseen_events(s, mia.id)] == ["zins"]
    ledger.mark_seen(s, mia.id)
    assert ledger.unseen_events(s, mia.id) == []


def test_statement_running_balance(s, kids):
    mia, tom = kids
    fund(s, giro(s, mia), 1000)
    ledger.transfer(s, giro(s, mia), giro(s, tom), 300, D0)
    rows = ledger.statement(s, giro(s, mia))
    assert [(delta, bal) for _, delta, bal in rows] == [(-300, 700), (1000, 1000)]


def test_prices_deterministic_idempotent_and_floored(tmp_path):
    histories = []
    for name in ("a.db", "b.db"):  # two separate databases must agree, day by day
        with new_session(tmp_path, name) as s:
            market.add_stock(s, "BIKE", "Bike Co", "🚲", 1000, D0)
            market.update_prices(s, D0 + timedelta(days=60))
            market.update_prices(s, D0 + timedelta(days=60))  # second call changes nothing
            histories.append([p.price_cents for p in s.exec(select(PriceHistory).order_by(PriceHistory.date))])
    assert histories[0] == histories[1] and len(histories[0]) == 61  # one row per day, incl. the start day
    assert min(histories[0]) >= market.MIN_PRICE_CENTS
    assert all(abs(b - a) <= a * 0.051 + 1 for a, b in zip(histories[0], histories[0][1:]))


def test_buy_and_sell(s, kids):
    mia, _ = kids
    fund(s, giro(s, mia), 10_000)
    st = market.add_stock(s, "BIKE", "Bike Co", "🚲", 1000, D0)
    market.buy(s, mia.id, st.id, 3, D0)
    assert giro(s, mia).balance_cents == 7_000
    with pytest.raises(LedgerError, match="err.insufficient"):
        market.buy(s, mia.id, st.id, 8, D0)
    with pytest.raises(LedgerError, match="err.no_shares"):
        market.sell(s, mia.id, st.id, 4, D0)
    market.sell(s, mia.id, st.id, 3, D0)
    assert giro(s, mia).balance_cents == 10_000


def test_first_run_picks_chosen_weekday_and_day_of_month():
    wed = date(2026, 9, 16)  # a Wednesday
    assert ledger.first_run("weekly", 2, wed) == date(2026, 9, 23)  # same weekday: next week, not today
    assert ledger.first_run("weekly", 0, wed) == date(2026, 9, 21)
    assert ledger.first_run("monthly", 20, wed) == date(2026, 9, 20)
    assert ledger.first_run("monthly", 16, wed) == date(2026, 10, 16)
    assert ledger.first_run("monthly", 5, date(2026, 12, 30)) == date(2027, 1, 5)


JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def make_goal(s, user, **kw):
    args = {"name": "Lego", "emoji": "🧸", "target_cents": 500, "photo": None, **kw}
    return ledger.create_goal(s, user.id, **args)


def test_goal_validation_and_photo_roundtrip(s, kids):
    mia, _ = kids
    with pytest.raises(LedgerError, match="err.amount"):
        make_goal(s, mia, target_cents=0)
    with pytest.raises(LedgerError, match="err.goal_empty"):
        make_goal(s, mia, name="  ")
    with pytest.raises(LedgerError, match="err.photo_type"):
        make_goal(s, mia, photo=b"GIF89a")
    with pytest.raises(LedgerError, match="err.photo_size"):
        make_goal(s, mia, photo=JPEG + b"x" * ledger.MAX_PHOTO_BYTES)

    g = make_goal(s, mia, name="", photo=JPEG)  # a photo alone is enough
    s.expire_all()  # force a real read back from SQLite
    assert s.get(Goal, g.id).photo == JPEG


def test_goal_limit_counts_only_active_goals(s, kids):
    mia, tom = kids
    goals = [make_goal(s, mia) for _ in range(ledger.MAX_ACTIVE_GOALS)]
    with pytest.raises(LedgerError, match="err.goal_limit"):
        make_goal(s, mia)
    make_goal(s, tom)  # limit is per kid

    fund(s, giro(s, mia), 500)
    ledger.finish_goal(goals[0], giro(s, mia))
    make_goal(s, mia)  # a finished goal frees a slot


def test_goal_progress_reached_and_finish(s, kids):
    mia, _ = kids
    g = make_goal(s, mia)
    assert ledger.goal_progress(g, giro(s, mia)) == 0

    fund(s, giro(s, mia), 250)
    assert ledger.goal_progress(g, giro(s, mia)) == 50
    assert not ledger.goal_reached(g, giro(s, mia))
    with pytest.raises(LedgerError, match="err.goal_not_reached"):
        ledger.finish_goal(g, giro(s, mia))

    fund(s, giro(s, mia), 300)  # 550 of 500
    assert ledger.goal_progress(g, giro(s, mia)) == 100  # capped
    assert ledger.goal_reached(g, giro(s, mia))
    assert ledger.unseen_reached_goals(s, mia.id) == [g]

    ledger.mark_seen(s, mia.id)
    assert ledger.unseen_reached_goals(s, mia.id) == []  # celebrated once

    ledger.finish_goal(g, giro(s, mia))
    assert g.done_at and not ledger.goal_reached(g, giro(s, mia))
    assert ledger.goal_progress(g, giro(s, mia)) == 100


def test_goal_already_affordable_has_no_celebration(s, kids):
    mia, _ = kids
    fund(s, giro(s, mia), 1000)
    g = make_goal(s, mia)
    assert ledger.goal_reached(g, giro(s, mia))
    assert ledger.unseen_reached_goals(s, mia.id) == []


def test_week_summary_buckets_and_window(s, kids, kurz):
    from datetime import datetime, time
    mia, tom = kids
    sp = giro(s, mia)
    at = lambda days: datetime.combine(D0 - timedelta(days=days), time(9))  # noqa: E731
    ledger.post(s, None, sp, 500, "dauerauftrag", now=at(0))
    ledger.post(s, None, sp, 300, "dauerauftrag", now=at(6))  # oldest day still inside
    ledger.post(s, None, sp, 900, "dauerauftrag", now=at(7))  # too old
    ledger.post(s, None, sp, 40, "zins", now=at(1))
    ledger.post(s, None, sp, 700, "manual", now=at(2))  # parent deposit
    ledger.post(s, sp, None, 250, "manual", now=at(3))  # parent withdrawal
    ledger.post(s, sp, giro(s, tom), 150, "manual", now=at(3))  # gift to Tom
    fg = ledger.open_festgeld(s, sp, 200, kurz, D0)  # saving is neither income nor spending
    assert ledger.week_summary(s, sp, D0) == {"dauerauftrag": 800, "zins": 40, "other": 700, "spent": 400}
    assert ledger.week_summary(s, giro(s, tom), D0) == {"dauerauftrag": 0, "zins": 0, "other": 150, "spent": 0}
    assert fg.balance_cents == 200
