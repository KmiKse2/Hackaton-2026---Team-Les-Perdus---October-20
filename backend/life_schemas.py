"""Customer commands are validated independently of model-generated proposals."""
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai_schemas import AIModel

Amount = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Label = Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoalInput(Command):
    category: Label = "personal"
    title: Label
    description: str = Field(default="", max_length=600)
    motivation: str = Field(default="", max_length=400)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    target_amount: Amount | None = None
    amount_basis: Literal["goal_budget", "property_deposit", "property_price", "non_financial"] = "goal_budget"
    target_date: date | None = None
    allocated_savings: Amount = Decimal(0)
    monthly_contribution: Amount = Decimal(0)
    priority: int = Field(default=3, ge=1, le=5, strict=True)
    primary: bool = False
    flexibility: Literal["flexible", "fixed"] = "flexible"
    status: Literal["active", "paused", "completed"] = "active"

    @model_validator(mode="after")
    def nonfinancial(self):
        if self.amount_basis == "non_financial" and (self.target_amount is not None or self.allocated_savings or self.monthly_contribution):
            raise ValueError("Non-financial goals do not allocate money")
        return self


class GoalChange(GoalInput):
    expected_revision: int | None = Field(default=None, ge=1)


class Budget(Command):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    savings_pool: Amount | None = None
    emergency_reserve: Amount = Decimal(0)
    monthly_capacity: Amount | None = None


class Coverage(Command):
    label: Label
    scope: Label
    risks: list[Label] = Field(min_length=1, max_length=20)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def dates(self):
        if self.end_date < self.start_date:
            raise ValueError("Coverage dates are reversed")
        return self


class Preferences(Command):
    memory_enabled: bool = True
    proactivity: Literal["off", "low", "normal"] = "normal"
    recommendation_interval_hours: int = Field(default=24, ge=1, le=720)
    notifications: bool = True
    reminders: bool = True
    budget_optimization: bool = False
    financing_preference: Literal["saving", "compare", "unspecified"] = "unspecified"
    communication: Literal["in_app", "branch", "phone"] = "in_app"
    allowed_categories: list[Label] = Field(default_factory=list, max_length=30)
    protected_categories: list[Label] = Field(default_factory=list, max_length=30)
    declined_questions: list[Label] = Field(default_factory=list, max_length=30)
    subscription_increase_percent: int = Field(default=10, ge=1, le=100)
    income_change_percent: int = Field(default=10, ge=1, le=100)
    renewal_notice_days: int = Field(default=30, ge=1, le=180)
    budgets: list[Budget] = Field(default_factory=list, max_length=10)
    insurance_policies: list[Coverage] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def distinct(self):
        if len({b.currency for b in self.budgets}) != len(self.budgets):
            raise ValueError("Only one budget per currency")
        return self


class SubscriptionInput(Command):
    confirmed: bool = False
    frequency: Literal["monthly", "annual", "unknown"] = "unknown"
    usage_status: Literal["regularly", "occasionally", "rarely", "unused", "unknown", "do_not_ask"] = "unknown"
    importance: Literal["essential", "valued", "optional", "unknown"] = "unknown"
    customer_decision: Literal["keep", "review", "consider_cancelling", "undecided"] = "undecided"
    renewal_date: date | None = None
    goal_id: int | None = Field(default=None, ge=1)


class DecisionInput(Command):
    status: Literal["rejected", "dismissed", "postponed", "accepted"]
    postpone_days: int = Field(default=7, ge=1, le=365)


class MemoryChange(AIModel):
    key: Literal["housing_status", "region", "personal_constraints", "hobbies", "passions",
                 "interests", "ambitions", "life_priorities", "financial_constraints",
                 "insurance_coverage", "retirement_preferences"]
    value: str = Field(min_length=1, max_length=500)


class GoalProposal(AIModel):
    goal_id: int | None
    title: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=600)
    motivation: str = Field(max_length=400)
    target_amount: str | None
    target_date: str | None
    currency: str
    amount_basis: Literal["goal_budget", "property_deposit", "property_price", "non_financial"]


class ConversationOutput(AIModel):
    message: str = Field(min_length=1, max_length=2200)
    intent: str = Field(min_length=1, max_length=80)
    clarification_questions: list[str] = Field(max_length=3)
    goal_updates: list[GoalProposal] = Field(max_length=3)
    memory_updates: list[MemoryChange] = Field(max_length=5)
    financing_preference: Literal["saving", "compare"] | None
    ecosystem_service_ids: list[str] = Field(max_length=4)
    next_best_action: Literal["clarify", "review_proposals", "view_plan", "review_subscriptions", "none"]


class ConfirmProposal(Command):
    accept: bool


class ScenarioInput(Command):
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    monthly_capacity_reduction: Amount = Decimal(0)
    unexpected_expense: Amount = Decimal(0)
    annual_cost_growth_percent: Decimal = Field(default=Decimal(0), ge=0, le=20, max_digits=5, decimal_places=2, allow_inf_nan=False)


class ServiceRecord(Command):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,60}$")
    name: Label
    provider: Label
    ecosystem: Label
    description: str = Field(min_length=1, max_length=500)
    supported_goal_categories: list[Label] = Field(max_length=30)
    required_information: list[Label] = Field(max_length=20)
    verified_url: str | None = None
    verification_status: Literal["catalogue_only", "official_source"] = "catalogue_only"
    last_verified_at: date | None = None
    requires_customer_action: Literal[True] = True
    integration_status: Literal["not_connected"] = "not_connected"
    source: str = "Curated official information"
    source_records: list[dict[str, str]] = Field(default_factory=list)
    supported_intents: list[Label] = Field(default_factory=list, max_length=30)
    service_kind: Literal["information", "credit", "investment", "insurance", "technical", "unreviewed"] = "information"
    destination_url: str | None = None
    destination_status: Literal["verified", "unverified"] = "unverified"
    destination_verified_at: date | None = None

    @model_validator(mode="after")
    def verified(self):
        if self.verification_status == "official_source":
            if not self.last_verified_at or not self.verified_url:
                raise ValueError("Official verification requires a date and source")
            if not safe_official_url(self.verified_url):
                raise ValueError("Source must be an approved official domain")
        elif self.verified_url:
            raise ValueError("Unverified catalogue entries cannot supply verified links")
        if self.destination_status == "verified":
            if (self.verification_status != "official_source" or not self.destination_verified_at
                    or not self.destination_url or not safe_official_url(self.destination_url)):
                raise ValueError("Navigation requires a verified official destination")
        elif self.destination_url or self.destination_verified_at:
            raise ValueError("Unverified destinations must be empty")
        return self


def safe_official_url(value):
    """Static catalogue links only: no credentials, query payloads or redirect parameters."""
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname in
                {"www.kbc.be", "www.kbc.com", "www.kbcbrussels.be", "startit-x.com"}
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and not parsed.query and not parsed.fragment and not any(c.isspace() for c in value)
                and "\\" not in value)
    except (ValueError, TypeError):
        return False
