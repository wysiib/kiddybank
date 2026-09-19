"""Simulated stock market: deterministic random walk, caught up lazily like interest."""

import random
from datetime import date, timedelta

from sqlmodel import Session, select

from .ledger import LedgerError, ensure_up_to_date, get_account, post
from .models import Holding, PriceHistory, Stock

MIN_PRICE_CENTS = 100
MAX_DAILY_MOVE = 0.05


def _step(symbol: str, day: date, prev: int) -> int:
    # seeded by (symbol, day): same day always gives the same move, whichever request computes it
    move = random.Random(f"{symbol}:{day.isoformat()}").uniform(-MAX_DAILY_MOVE, MAX_DAILY_MOVE)
    return max(MIN_PRICE_CENTS, round(prev * (1 + move)))


def add_stock(s: Session, symbol: str, name: str, emoji: str, price_cents: int, today: date) -> Stock:
    st = Stock(symbol=symbol, name=name, emoji=emoji, current_price_cents=price_cents, last_updated=today)
    s.add(st)
    s.flush()
    s.add(PriceHistory(stock_id=st.id, date=today, price_cents=price_cents))
    s.flush()
    return st


def update_prices(s: Session, today: date) -> None:
    for st in s.exec(select(Stock)).all():
        while st.last_updated < today:
            day = st.last_updated + timedelta(days=1)
            known = s.exec(select(PriceHistory).where(PriceHistory.stock_id == st.id, PriceHistory.date == day)).first()
            if known:
                price = known.price_cents
            else:
                price = _step(st.symbol, day, st.current_price_cents)
                s.add(PriceHistory(stock_id=st.id, date=day, price_cents=price))
            st.current_price_cents, st.last_updated = price, day
    s.flush()


def _holding(s: Session, user_id: int, stock_id: int) -> Holding | None:
    return s.exec(select(Holding).where(Holding.user_id == user_id, Holding.stock_id == stock_id)).first()


def buy(s: Session, user_id: int, stock_id: int, shares: int, today: date) -> None:
    if shares <= 0:
        raise LedgerError("err.amount")
    update_prices(s, today)
    st, giro = s.get(Stock, stock_id), get_account(s, user_id, "giro")
    ensure_up_to_date(s, giro, today)
    cost = shares * st.current_price_cents
    post(s, giro, None, cost, "aktienkauf", note=st.symbol)
    h = _holding(s, user_id, stock_id)
    if h:
        h.avg_buy_price_cents = (h.shares * h.avg_buy_price_cents + cost) // (h.shares + shares)
        h.shares += shares
    else:
        s.add(Holding(user_id=user_id, stock_id=stock_id, shares=shares, avg_buy_price_cents=st.current_price_cents))
    s.flush()


def sell(s: Session, user_id: int, stock_id: int, shares: int, today: date) -> None:
    h = _holding(s, user_id, stock_id)
    if shares <= 0:
        raise LedgerError("err.amount")
    if not h or h.shares < shares:
        raise LedgerError("err.no_shares")
    update_prices(s, today)
    st, giro = s.get(Stock, stock_id), get_account(s, user_id, "giro")
    ensure_up_to_date(s, giro, today)
    post(s, None, giro, shares * st.current_price_cents, "aktienverkauf", note=st.symbol)
    h.shares -= shares
    if h.shares == 0:
        s.delete(h)
    s.flush()
