from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import events, festgeld, goals, ledger, market
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
    return festgeld.add_product(s, "Kurz", 7, 1200)


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
    assert sp.balance_cents == 10_000 + 77  # paid on days 7, 14, 21 and 28 (19+19+19+20); days 29-30 are still accruing
    assert sp.interest_paid_on == D0 + timedelta(days=28)
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=30))
    assert sp.balance_cents == 10_077  # catching up twice books once
    days = [tx.timestamp.date() for tx in s.exec(select(Transaction).where(Transaction.type == "zins").order_by(Transaction.id))]
    assert days == [D0 + timedelta(days=d) for d in (7, 14, 21, 28)]  # dated when they fell due, not when somebody looked


def test_visit_pattern_does_not_change_the_result(s):
    def run(name, visits):
        kid = ledger.create_user(s, name, "child", "1111", today=D0, giro_rate_bp=1000)
        sp = giro(s, kid)
        sp.balance_cents = 10_000
        s.add(RecurringRule(from_account_id=None, to_account_id=sp.id, amount_cents=500, interval="weekly", next_run=D0 + timedelta(days=3)))
        s.flush()
        for d in visits:
            ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=d))
        return sp.balance_cents

    assert run("Daily", range(1, 61)) == run("Once", [60]) == run("Rare", [17, 60])


def test_monthly_and_yearly_payout_periods(s):
    ann = ledger.create_user(s, "Ann", "child", "1111", today=D0, giro_rate_bp=1000, payout_days=30)
    sp = giro(s, ann)
    sp.balance_cents = 10_000
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=29))
    assert sp.balance_cents == 10_000  # a week has passed four times, but she is paid monthly
    assert ledger.next_interest(sp, D0 + timedelta(days=29)) == (1, 82)  # 30 days of 10 %/year on 100 EUR
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=30))
    assert sp.balance_cents == 10_082

    ledger.set_giro_rate(s, sp, 1000, D0 + timedelta(days=30), payout_days=365)  # switching settles first
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=364))
    assert sp.balance_cents == 10_082
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=395))
    assert sp.balance_cents == 10_082 + 1008  # 10082 * 10 % * 365/365 = 1008.2 cents
    with pytest.raises(LedgerError, match="err.rate"):
        ledger.set_giro_rate(s, sp, 1000, D0 + timedelta(days=396), payout_days=14)


def test_rate_per_period_converts_to_annual_and_back():
    assert ledger.annual_bp(100, 7) == 5214  # 1 % per week
    for days in ledger.PAYOUT_PERIODS:
        for bp in (50, 100, 300, 10_000):
            assert ledger.period_bp(ledger.annual_bp(bp, days), days) == bp  # what the parent typed comes back
    assert ledger.annual_bp(10_000, 7) <= ledger.MAX_RATE_BP < ledger.annual_bp(10_100, 7)  # cap: 100 % per week


def test_one_percent_per_week_pays_exactly_one_percent(s):
    kid = ledger.create_user(s, "Ann", "child", "1111", today=D0)  # default: 1 % per week
    sp = giro(s, kid)
    sp.balance_cents = 10_000
    assert ledger.next_interest(sp, D0)[1] == 100  # 5214 bp/year is 1 % minus a hair, still 1,00 EUR
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=7))
    assert sp.balance_cents == 10_100
    assert ledger.interest_cents(10_000, ledger.annual_bp(150, 7), 7) == 150  # same for Festgeld


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
    paid = lambda: [tx.amount_cents for tx in s.exec(select(Transaction).where(Transaction.type == "dauerauftrag"))]  # noqa: E731
    ledger.ensure_up_to_date(s, g, D0 + timedelta(days=21))
    assert paid() == [500, 500, 500]
    ledger.ensure_up_to_date(s, g, D0 + timedelta(days=21))
    assert paid() == [500, 500, 500]


def test_monthly_rule_clamps_to_month_end():
    assert ledger._next_run(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert ledger._next_run(date(2026, 12, 15), "monthly") == date(2027, 1, 15)


def test_festgeld_lock_collect_and_payout(s, kids, kurz):
    mia, _ = kids
    fund(s, giro(s, mia), 10_000)
    fg = festgeld.open_festgeld(s, giro(s, mia), 5_000, kurz, D0)
    assert giro(s, mia).balance_cents == 5_000 and fg.balance_cents == 5_000
    assert festgeld.festgeld_status(fg, D0) == "locked"

    with pytest.raises(LedgerError, match="err.festgeld_locked"):
        festgeld.collect_festgeld(s, fg, D0 + timedelta(days=6))
    with pytest.raises(LedgerError, match="err.festgeld_locked"):  # no other way out either
        ledger.transfer(s, fg, giro(s, mia), 100, D0 + timedelta(days=8))

    maturity = D0 + timedelta(days=7)
    assert festgeld.festgeld_status(fg, maturity) == "ready"
    total, interest = festgeld.festgeld_payout(fg)
    assert interest == 12  # 5000 * 12% * 7/365 = 11.51 cents, rounded to the nearest cent
    assert festgeld.collect_festgeld(s, fg, maturity) == (total, interest) == (5_012, 12)
    # the Giro is settled before the payout lands: its 50 EUR earned 9.59 -> 10 cents of its own over the week
    assert giro(s, mia).balance_cents == 5_000 + 10 + 5_012 and fg.balance_cents == 0
    assert festgeld.festgeld_status(fg, maturity) == "collected"

    with pytest.raises(LedgerError, match="err.already_collected"):
        festgeld.collect_festgeld(s, fg, maturity)


def test_several_deposits_at_once_keep_their_own_rate(s, kids, kurz):
    mia, _ = kids
    lang = festgeld.add_product(s, "Lang", 30, 2000)
    fund(s, giro(s, mia), 10_000)
    a = festgeld.open_festgeld(s, giro(s, mia), 3_000, kurz, D0)
    b = festgeld.open_festgeld(s, giro(s, mia), 4_000, lang, D0)
    assert giro(s, mia).balance_cents == 3_000
    assert (a.name, a.interest_rate_bp, b.name, b.interest_rate_bp) == ("Kurz", 1200, "Lang", 2000)

    lang.rate_bp = 500  # a later rate change must not touch open deposits
    assert b.interest_rate_bp == 2000
    assert festgeld.festgeld_payout(b)[1] == 66  # 4000 * 20% * 30/365 = 65.75 cents, the old rate

    festgeld.collect_festgeld(s, a, D0 + timedelta(days=7))  # collecting one leaves the other locked
    assert festgeld.festgeld_status(b, D0 + timedelta(days=7)) == "locked"


def test_rate_change_settles_interest_at_the_old_rate_first(s, kids):
    g = giro(s, kids[0])
    g.balance_cents = 10_000
    ledger.set_giro_rate(s, g, 0, D0 + timedelta(days=14))
    assert g.balance_cents == 10_000 + 38  # 10000 * 10% * 14/365 = 38.4 cents, at the old 10 %
    ledger.ensure_up_to_date(s, g, D0 + timedelta(days=60))
    assert g.balance_cents == 10_038  # new rate 0: nothing more


def test_rates_and_products_are_validated(s, kids):
    for bad in (-1, ledger.MAX_RATE_BP + 1):
        with pytest.raises(LedgerError, match="err.rate"):
            ledger.set_giro_rate(s, giro(s, kids[0]), bad, D0)
        with pytest.raises(LedgerError, match="err.rate"):
            festgeld.add_product(s, "X", 7, bad)
    with pytest.raises(LedgerError, match="err.rate"):
        festgeld.add_product(s, "X", 7, 100, rate_days=14)
    assert festgeld.add_product(s, "X", 7, 100, rate_days=7).rate_days == 7
    with pytest.raises(LedgerError, match="err.term"):
        festgeld.add_product(s, "X", 0, 100)
    with pytest.raises(LedgerError, match="err.name"):
        festgeld.add_product(s, "  ", 7, 100)


def test_default_products_are_seeded_once(s):
    festgeld.seed_default_products(s)
    festgeld.seed_default_products(s)
    assert [(p.name, p.term_days, p.rate_bp) for p in s.exec(select(FestgeldProduct).order_by(FestgeldProduct.id))] == \
        list(festgeld.DEFAULT_PRODUCTS)


def test_celebrations_show_once(s, kids):
    mia, _ = kids
    sp = giro(s, mia)
    sp.balance_cents = 10_000
    ledger.ensure_up_to_date(s, sp, D0 + timedelta(days=30))
    assert [t.type for t in events.unseen_events(s, mia.id)] == ["zins"] * 4  # the app shows them as one celebration
    events.mark_seen(s, mia.id, D0)
    assert events.unseen_events(s, mia.id) == []


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
    return goals.create_goal(s, user.id, today=D0, **args)


def test_goal_validation_and_photo_roundtrip(s, kids):
    mia, _ = kids
    with pytest.raises(LedgerError, match="err.amount"):
        make_goal(s, mia, target_cents=0)
    with pytest.raises(LedgerError, match="err.goal_empty"):
        make_goal(s, mia, name="  ")
    with pytest.raises(LedgerError, match="err.photo_type"):
        make_goal(s, mia, photo=b"GIF89a")
    with pytest.raises(LedgerError, match="err.photo_size"):
        make_goal(s, mia, photo=JPEG + b"x" * goals.MAX_PHOTO_BYTES)

    g = make_goal(s, mia, name="", photo=JPEG)  # a photo alone is enough
    s.expire_all()  # force a real read back from SQLite
    assert s.get(Goal, g.id).photo == JPEG


def test_goal_limit_counts_only_active_goals(s, kids):
    mia, tom = kids
    made = [make_goal(s, mia) for _ in range(goals.MAX_ACTIVE_GOALS)]
    with pytest.raises(LedgerError, match="err.goal_limit"):
        make_goal(s, mia)
    make_goal(s, tom)  # limit is per kid

    fund(s, giro(s, mia), 500)
    goals.finish_goal(made[0], giro(s, mia), D0)
    make_goal(s, mia)  # a finished goal frees a slot


def test_goal_progress_reached_and_finish(s, kids):
    mia, _ = kids
    g = make_goal(s, mia)
    assert goals.goal_progress(g, giro(s, mia)) == 0

    fund(s, giro(s, mia), 250)
    assert goals.goal_progress(g, giro(s, mia)) == 50
    assert not goals.goal_reached(g, giro(s, mia))
    with pytest.raises(LedgerError, match="err.goal_not_reached"):
        goals.finish_goal(g, giro(s, mia), D0)

    fund(s, giro(s, mia), 300)  # 550 of 500
    assert goals.goal_progress(g, giro(s, mia)) == 100  # capped
    assert goals.goal_reached(g, giro(s, mia))
    assert goals.unseen_reached_goals(s, mia.id) == [g]

    events.mark_seen(s, mia.id, D0)
    assert goals.unseen_reached_goals(s, mia.id) == []  # celebrated once

    goals.finish_goal(g, giro(s, mia), D0)
    assert g.done_at and not goals.goal_reached(g, giro(s, mia))
    assert goals.goal_progress(g, giro(s, mia)) == 100


def test_goal_already_affordable_has_no_celebration(s, kids):
    mia, _ = kids
    fund(s, giro(s, mia), 1000)
    g = make_goal(s, mia)
    assert goals.goal_reached(g, giro(s, mia))
    assert goals.unseen_reached_goals(s, mia.id) == []


def test_week_summary_buckets_and_window(s, kids, kurz):
    from datetime import datetime, time
    mia, tom = kids
    sp = giro(s, mia)
    at = lambda days: datetime.combine(D0 - timedelta(days=days), time(9))  # noqa: E731
    ledger.post(s, None, sp, 500, "dauerauftrag", D0, now=at(0))
    ledger.post(s, None, sp, 300, "dauerauftrag", D0, now=at(6))  # oldest day still inside
    ledger.post(s, None, sp, 900, "dauerauftrag", D0, now=at(7))  # too old
    ledger.post(s, None, sp, 40, "zins", D0, now=at(1))
    ledger.post(s, None, sp, 700, "manual", D0, now=at(2))  # parent deposit
    ledger.post(s, sp, None, 250, "manual", D0, now=at(3))  # parent withdrawal
    ledger.post(s, sp, giro(s, tom), 150, "manual", D0, now=at(3))  # gift to Tom
    fg = festgeld.open_festgeld(s, sp, 200, kurz, D0)  # saving is neither income nor spending
    assert ledger.week_summary(s, sp, D0) == {"dauerauftrag": 800, "zins": 40, "other": 700, "spent": 400}
    assert ledger.week_summary(s, giro(s, tom), D0) == {"dauerauftrag": 0, "zins": 0, "other": 150, "spent": 0}
    assert fg.balance_cents == 200


def test_older_db_gets_added_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, user_id INTEGER)")  # a DB from before payout_days
    db.commit(); db.close()
    make_engine(f"sqlite:///{path}")
    cols = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(account)")}
    assert "payout_days" in cols
    make_engine(f"sqlite:///{path}")  # idempotent


def test_failed_operations_change_nothing(s, kids, kurz):
    """Routes rely on this: after a LedgerError there is nothing to roll back."""
    mia, tom = kids
    fund(s, giro(s, mia), 1000)
    before = lambda: (giro(s, mia).balance_cents, giro(s, tom).balance_cents, len(s.exec(select(Transaction)).all()))  # noqa: E731
    state = before()
    for bad in (lambda: ledger.transfer(s, giro(s, mia), giro(s, tom), 5000, D0),
                lambda: ledger.transfer(s, giro(s, mia), giro(s, tom), -5, D0),
                lambda: ledger.manual_booking(s, giro(s, mia), -5000, D0),
                lambda: festgeld.open_festgeld(s, giro(s, mia), 5000, kurz, D0)):
        with pytest.raises(LedgerError):
            bad()
        assert before() == state
    assert s.exec(select(ledger.Account).where(ledger.Account.type == "festgeld")).all() == []


def test_goal_lists_do_not_load_photos(s, kids):
    from sqlalchemy import inspect
    uid = kids[0].id
    goals.create_goal(s, uid, "Lego", "🧸", 2000, b"\xff\xd8\xff" + b"x" * 1000, D0)
    s.commit()
    s.expunge_all()
    goal = goals.list_goals(s, uid)[0]
    assert goal.has_photo and "photo" in inspect(goal).unloaded  # the BLOB stays in the DB until asked for
    assert s.get(Goal, goal.id).photo.startswith(b"\xff\xd8\xff")


def test_older_db_marks_existing_photos(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE goal (id INTEGER PRIMARY KEY, photo BLOB)")
    db.execute("INSERT INTO goal (photo) VALUES (x'ffd8ff'), (NULL)")
    db.commit(); db.close()
    make_engine(f"sqlite:///{path}")
    assert sqlite3.connect(path).execute("SELECT has_photo FROM goal ORDER BY id").fetchall() == [(1,), (0,)]


def test_rules_are_validated_and_edits_settle_the_old_schedule_first(s, kids):
    mia, _ = kids
    g = giro(s, mia)
    for bad in (dict(cents=0), dict(cents=ledger.MAX_CENTS + 1), dict(interval="daily"), dict(weekday=7), dict(monthday=29)):
        with pytest.raises(LedgerError, match="err.amount"):
            ledger.add_rule(s, g, **{"cents": 500, "interval": "weekly", "weekday": 0, "monthday": 1, "today": D0, **bad})
    assert s.exec(select(RecurringRule)).all() == []
    r = ledger.add_rule(s, g, 500, "weekly", 4, 1, D0)
    assert r.next_run.weekday() == 4 and r.next_run > D0
    ledger.update_rule(s, r, 300, "monthly", 0, 15, r.next_run + timedelta(days=1))  # a payday has passed unnoticed
    assert g.balance_cents == 500  # the old 5 EUR was paid out before the schedule moved
    assert (r.amount_cents, r.interval, r.next_run.day) == (300, "monthly", 15)
