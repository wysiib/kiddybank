from datetime import date, datetime

from sqlalchemy import CheckConstraint, UniqueConstraint, event
from sqlmodel import Field, SQLModel, create_engine


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    role: str  # parent | child
    pin_hash: str
    avatar: str = "🐷"
    # per-kid modules, toggled by a parent; enforced in the routes, existing deposits/holdings stay reachable
    festgeld_enabled: bool = True
    stocks_enabled: bool = True


class FestgeldProduct(SQLModel, table=True):
    """Parent-managed offer. Never deleted, only deactivated, so old deposits keep their history."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    term_days: int
    rate_bp: int  # basis points per year
    active: bool = True


class Account(SQLModel, table=True):
    __table_args__ = (CheckConstraint("balance_cents >= 0 OR allow_overdraft"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: str  # giro | festgeld
    name: str = ""  # festgeld: product name at the time it was opened
    balance_cents: int = 0
    interest_rate_bp: int = 0  # basis points per year; fixed at opening for festgeld
    allow_overdraft: bool = False
    last_updated: date  # interest accrued up to this day
    interest_accrued: int = 0  # cent * 10000 * 365, exact remainder carried between payouts
    interest_paid_on: date  # last day interest was booked
    opened_at: date | None = None  # festgeld only
    maturity_date: date | None = None  # festgeld only
    collected_at: datetime | None = None  # festgeld only


class Transaction(SQLModel, table=True):
    __table_args__ = (CheckConstraint("from_account_id IS NOT NULL OR to_account_id IS NOT NULL"),)

    id: int | None = Field(default=None, primary_key=True)
    # NULL side = virtual parent/bank/market (pocket money, interest, stock trades)
    from_account_id: int | None = Field(default=None, foreign_key="account.id", index=True)
    to_account_id: int | None = Field(default=None, foreign_key="account.id", index=True)
    amount_cents: int
    type: str  # manual | dauerauftrag | zins | festgeld | aktienkauf | aktienverkauf
    timestamp: datetime
    note: str = ""
    seen_at: datetime | None = None  # drives the celebration screens


class RecurringRule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    from_account_id: int | None = Field(default=None, foreign_key="account.id")
    to_account_id: int = Field(foreign_key="account.id", index=True)
    amount_cents: int
    interval: str  # weekly | monthly
    next_run: date


class Stock(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(unique=True)
    name: str
    emoji: str = "📈"
    current_price_cents: int
    last_updated: date


class PriceHistory(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("stock_id", "date"),)

    id: int | None = Field(default=None, primary_key=True)
    stock_id: int = Field(foreign_key="stock.id", index=True)
    date: date
    price_cents: int


class Holding(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "stock_id"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    stock_id: int = Field(foreign_key="stock.id")
    shares: int
    avg_buy_price_cents: int


def make_engine(url: str = "sqlite:///kiddybank.db"):
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        conn.isolation_level = None  # we emit BEGIN ourselves, see below
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def _begin(conn):
        # writers serialize up front, so lazy catch-up on a GET can't race another request
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    SQLModel.metadata.create_all(engine)
    return engine
