"""Local adapter contract based on the supplied fields, not a certified KBC SDK."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Money(WireModel):
    amount: Decimal = Field(max_digits=20, decimal_places=8, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class AccountDetails(WireModel):
    resourceId: str = Field(min_length=1, max_length=100)
    iban: str | None = Field(default=None, max_length=40)
    bban: str | None = Field(default=None, max_length=40)
    pan: str | None = Field(default=None, max_length=30)
    msisdn: str | None = Field(default=None, max_length=30)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    name: str | None = Field(default=None, max_length=120)
    ownerName: str | None = Field(default=None, max_length=200)
    product: str | None = Field(default=None, max_length=100)
    cashAccountType: str | None = Field(default=None, max_length=8)
    status: Literal["enabled", "deleted", "blocked"]
    bic: str | None = Field(default=None, max_length=11)
    usage: Literal["PRIV", "ORG"] | None = None


class AccountBalance(WireModel):
    balanceAmount: Money
    balanceType: Literal["interimAvailable", "closingBooked", "expected", "interimBooked"]
    lastChangeDateTime: AwareDatetime | None = None
    referenceDate: date
    creditLimitIncluded: bool | None = None


class CounterpartyAccount(WireModel):
    iban: str | None = Field(default=None, max_length=40)
    bban: str | None = Field(default=None, max_length=40)


class ReportTransaction(WireModel):
    transactionId: str = Field(min_length=1, max_length=100)
    endToEndId: str | None = Field(default=None, max_length=100)
    mandateId: str | None = Field(default=None, max_length=100)
    transactionAmount: Money
    creditorName: str | None = Field(default=None, max_length=200)
    debtorName: str | None = Field(default=None, max_length=200)
    creditorAccount: CounterpartyAccount | None = None
    debtorAccount: CounterpartyAccount | None = None
    creditorAgent: str | None = Field(default=None, max_length=11)
    debtorAgent: str | None = Field(default=None, max_length=11)
    bookingDate: date | None = None
    valueDate: date | None = None
    remittanceInformationUnstructured: str | None = Field(default=None, max_length=500)
    remittanceInformationStructured: str | None = Field(default=None, max_length=200)
    bankTransactionCode: dict[str, str] | str | None = None
    purposeCode: str | None = Field(default=None, pattern=r"^[A-Z0-9]{4}$")
    merchantCountry: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class AccountReport(WireModel):
    booked: list[ReportTransaction] = Field(default_factory=list, max_length=5000)
    pending: list[ReportTransaction] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_report(self):
        ids = [t.transactionId for t in self.booked + self.pending]
        if len(ids) != len(set(ids)):
            raise ValueError("transactionId must be unique across booked and pending")
        if any(t.bookingDate is None for t in self.booked):
            raise ValueError("A booked transaction requires bookingDate")
        return self


class AccountAccess(WireModel):
    consentId: str = Field(min_length=1, max_length=100)
    consentStatus: Literal["valid", "revokedByPsu", "expired", "terminatedByTpp"]
    validUntil: date
    frequencyPerDay: int = Field(ge=0, le=10000)
    permissions: list[Literal["accounts", "balances", "transactions"]] = Field(max_length=3)
    accountResourceIds: list[str] = Field(max_length=100)


class AccountImport(WireModel):
    details: AccountDetails
    balances: list[AccountBalance] | None = Field(default=None, max_length=4)
    balanceHistory: list[AccountBalance] | None = Field(default=None, max_length=2000)
    transactions: AccountReport | None = None
    historyFrom: date | None = None
    historyTo: date | None = None

    @model_validator(mode="after")
    def consistency(self):
        if self.balanceHistory is not None:
            keys = [(b.balanceType, b.referenceDate) for b in self.balanceHistory]
            if len(set(keys)) != len(keys) or any(b.balanceAmount.currency != self.details.currency for b in self.balanceHistory):
                raise ValueError("Duplicate balance history or inconsistent currency")
        if self.balances is not None:
            if len({b.balanceType for b in self.balances}) != len(self.balances):
                raise ValueError("Only one snapshot per balance type is allowed")
            if any(b.balanceAmount.currency != self.details.currency for b in self.balances):
                raise ValueError("Balance currency differs from account currency")
        if self.transactions is not None:
            if not self.historyFrom or not self.historyTo or self.historyFrom > self.historyTo:
                raise ValueError("A complete report requires consistent historyFrom and historyTo")
            for tx in self.transactions.booked + self.transactions.pending:
                if tx.transactionAmount.currency != self.details.currency:
                    raise ValueError("Transaction currency differs from account currency")
            if any(not self.historyFrom <= t.bookingDate <= self.historyTo for t in self.transactions.booked):
                raise ValueError("Transaction outside the declared report period")
        return self


class BankingImport(WireModel):
    consentId: str = Field(min_length=1, max_length=100)
    asOf: date
    accounts: list[AccountImport] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def consistency(self):
        if len({a.details.resourceId for a in self.accounts}) != len(self.accounts):
            raise ValueError("Duplicate account")
        if any(a.historyTo and a.historyTo > self.asOf for a in self.accounts):
            raise ValueError("historyTo must be on or before asOf")
        if any(b.referenceDate > self.asOf for a in self.accounts for b in (a.balances or []) + (a.balanceHistory or [])):
            raise ValueError("Balance date is later than asOf")
        return self
