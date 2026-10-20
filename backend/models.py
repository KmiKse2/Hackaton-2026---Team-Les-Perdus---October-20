from datetime import date as DateValue
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    persona: Mapped[str] = mapped_column(String(120))
    initials: Mapped[str] = mapped_column(String(2))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("customer_id", "reference"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    date: Mapped[DateValue] = mapped_column(Date)
    merchant: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    category: Mapped[str] = mapped_column(String(30))
    reference: Mapped[str] = mapped_column(String(100))


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (UniqueConstraint("customer_id", "type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    score: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="pending")
    evidence: Mapped[list] = mapped_column(JSON)
    personalization: Mapped[dict] = mapped_column(JSON)


# Additive V2 tables: V1 transactions are preserved and imported once.
class BankProfile(Base):
    __tablename__ = "bank_profiles"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    as_of: Mapped[DateValue] = mapped_column(Date)


class Account(Base):
    __tablename__ = "bank_accounts"
    resource_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    details: Mapped[dict] = mapped_column(JSON)
    history_from: Mapped[DateValue | None] = mapped_column(Date)
    history_to: Mapped[DateValue | None] = mapped_column(Date)


class Balance(Base):
    __tablename__ = "bank_balances"
    __table_args__ = (UniqueConstraint("account_id", "balance_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.resource_id"), index=True)
    balance_type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("account_id", "transaction_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.resource_id"), index=True)
    transaction_id: Mapped[str] = mapped_column(String(100))
    booking_status: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict] = mapped_column(JSON)


class Consent(Base):
    __tablename__ = "bank_consents"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    consent_id: Mapped[str] = mapped_column(String(100), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    account_ids: Mapped[list] = mapped_column(JSON)
    sync_day: Mapped[DateValue | None] = mapped_column(Date)
    sync_count: Mapped[int] = mapped_column(default=0)


class CustomerContext(Base):
    __tablename__ = "customer_contexts"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    products: Mapped[dict | None] = mapped_column(JSON)
    usage: Mapped[dict | None] = mapped_column(JSON)
    personalization: Mapped[dict] = mapped_column(JSON)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class BalanceObservation(Base):
    __tablename__ = "balance_observations"
    __table_args__ = (UniqueConstraint("account_id", "balance_type", "reference_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.resource_id"), index=True)
    balance_type: Mapped[str] = mapped_column(String(30))
    reference_date: Mapped[DateValue] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSON)


class CategoryFeedback(Base):
    __tablename__ = "category_feedback"
    __table_args__ = (UniqueConstraint("customer_id", "category"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    observed_at: Mapped[DateValue] = mapped_column(Date)
    valid_until: Mapped[DateValue] = mapped_column(Date)


class SimulationMonth(Base):
    __tablename__ = "simulation_months"
    __table_args__ = (UniqueConstraint("customer_id", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))
    summary: Mapped[dict] = mapped_column(JSON)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(String(3000))
    proposal_id: Mapped[str | None] = mapped_column(String(80))
    as_of: Mapped[DateValue] = mapped_column(Date)
