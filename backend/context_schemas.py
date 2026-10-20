from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import Field, StrictBool, StrictInt, model_validator

from .schemas import Money, WireModel

T = TypeVar("T")


class Fact(WireModel, Generic[T]):
    value: T
    source: Literal["kyc", "declared", "product_database", "kate_confirmation"]
    observed_at: date
    valid_until: date | None = None

    @model_validator(mode="after")
    def dates(self):
        if self.valid_until and self.valid_until < self.observed_at:
            raise ValueError("Validity must follow the observation date")
        return self


class ProfileAttributes(WireModel):
    date_of_birth: Fact[date] | None = None
    employment_status: Fact[Literal["student", "employed", "self_employed", "unemployed", "retired", "unknown"]] | None = None
    occupation: Fact[str] | None = None
    industry: Fact[Literal["IT", "healthcare", "education", "construction", "finance", "other", "unknown"]] | None = None
    monthly_income: Fact[Money] | None = None
    household_status: Fact[Literal["single", "married", "cohabiting", "divorced", "unknown"]] | None = None
    dependents_count: Fact[StrictInt] | None = None
    investment_risk_profile: Fact[Literal["conservative", "balanced", "dynamic", "unknown"]] | None = None
    financial_goal: Fact[Literal["save_for_house", "build_emergency_fund", "travel", "invest", "buy_car", "other"]] | None = None
    preferred_channel: Fact[Literal["mobile", "web", "branch", "phone", "Kate"]] | None = None

    @model_validator(mode="after")
    def consistency(self):
        if self.date_of_birth and self.date_of_birth.value > self.date_of_birth.observed_at:
            raise ValueError("Date of birth is later than its collection date")
        if self.dependents_count and not 0 <= self.dependents_count.value <= 30:
            raise ValueError("Invalid number of dependants")
        if self.investment_risk_profile and not self.investment_risk_profile.valid_until:
            raise ValueError("Investment risk profile requires an expiry date")
        if self.monthly_income and self.monthly_income.value.amount < 0:
            raise ValueError("Declared income cannot be negative")
        for field in (self.occupation,):
            if field and not 1 <= len(field.value) <= 120:
                raise ValueError("Invalid profile label")
        return self


class Product(WireModel):
    id: str = Field(min_length=1, max_length=100)
    type: Literal["current_account", "savings_account", "credit_card", "insurance", "mortgage", "car_loan", "personal_loan", "investments"]
    status: Literal["active", "closed"]
    started_at: date
    monthly_repayment: Money | None = None
    valuation: Money | None = None

    @model_validator(mode="after")
    def amounts(self):
        if self.monthly_repayment and self.monthly_repayment.amount < 0:
            raise ValueError("Negative monthly repayment")
        return self


class ProductSnapshot(WireModel):
    observed_at: date
    valid_until: date
    complete: StrictBool
    items: list[Product] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.valid_until < self.observed_at or any(p.started_at > self.observed_at for p in self.items):
            raise ValueError("Inconsistent product snapshot dates")
        if len({p.id for p in self.items}) != len(self.items):
            raise ValueError("Duplicate product")
        return self


class AppSession(WireModel):
    id: str = Field(min_length=1, max_length=100)
    date: date
    channel: Literal["mobile", "web", "branch", "phone", "Kate"]


class KateInteraction(WireModel):
    id: str = Field(min_length=1, max_length=100)
    date: date
    topic: Literal["budget", "housing", "travel", "income", "savings", "other"]
    action: Literal["view", "ask", "confirm", "dismiss"]
    confirmation: StrictBool | None = None
    outcome: Literal["completed", "pending", "declined"]


class UsageSnapshot(WireModel):
    from_date: date
    to_date: date
    sessions: list[AppSession] = Field(default_factory=list, max_length=2000)
    kate_interactions: list[KateInteraction] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def coverage(self):
        if self.from_date > self.to_date:
            raise ValueError("Reversed date range")
        for rows in (self.sessions, self.kate_interactions):
            if any(not self.from_date <= r.date <= self.to_date for r in rows) or len({r.id for r in rows}) != len(rows):
                raise ValueError("Duplicate or out-of-period log entry")
        return self


class ContextImport(WireModel):
    attributes: ProfileAttributes | None = None
    products: ProductSnapshot | None = None
    usage: UsageSnapshot | None = None


class PersonalizationConsent(Fact[StrictBool]):
    pass


class CategoryResponse(WireModel):
    status: Literal["confirmed", "rejected"]
