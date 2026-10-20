"""The 40 signals and four cross-cutting metadata fields in the supplied catalog."""
SIGNALS = {
    "age_group": ("Age group", "kyc"),
    "employment_status": ("Employment status", "declared"),
    "occupation": ("Occupation", "declared"),
    "industry": ("Industry", "declared"),
    "monthly_income": ("Recurring monthly income", "transaction"),
    "income_change_percentage": ("Income change", "transaction"),
    "income_source_type": ("Income sources", "transaction"),
    "new_recurring_salary": ("New recurring salary", "transaction"),
    "salary_stopped": ("Expected salary not observed", "transaction"),
    "student_grant_detected": ("Possible student grant", "transaction"),
    "household_status": ("Household status", "declared"),
    "dependents_count": ("Dependants", "declared"),
    "current_account_balance": ("Current account balance", "account"),
    "savings_balance": ("Savings balance", "account"),
    "monthly_savings_rate": ("Savings transfer rate", "transaction"),
    "savings_trend": ("Savings trend", "account"),
    "recurring_expenses": ("Recurring expenses", "transaction"),
    "new_rent_payment": ("New recurring rent", "transaction"),
    "housing_spending_increase": ("Housing spending change", "transaction"),
    "travel_spending_detected": ("Travel spending", "transaction"),
    "foreign_transaction_activity": ("Foreign currency or country activity", "transaction"),
    "unusual_spending": ("Unusual spending", "transaction"),
    "spending_category_shift": ("Spending distribution change", "transaction"),
    "monthly_expenses": ("Monthly expenses", "transaction"),
    "expense_change_percentage": ("Expense change", "transaction"),
    "overdraft_frequency": ("Negative balance frequency", "account"),
    "low_balance_risk": ("Projected balance before next income", "inferred"),
    "savings_withdrawal_detected": ("Large savings withdrawal", "transaction"),
    "loan_products": ("Existing loans", "product_database"),
    "monthly_loan_commitment": ("Monthly loan commitments", "product_database"),
    "mortgage_started": ("New mortgage", "product_database"),
    "owned_kbc_products": ("Owned products", "product_database"),
    "investment_balance": ("Investment valuation", "product_database"),
    "investment_risk_profile": ("Declared investment risk profile", "declared"),
    "financial_goal": ("Declared goal", "declared"),
    "preferred_channel": ("Preferred channel", "declared"),
    "mobile_usage_frequency": ("App usage", "app_log"),
    "kate_interaction_history": ("Structured Kate interactions", "kate_log"),
    "customer_confirmed_event": ("Customer responses", "kate_confirmation"),
    "personalization_consent": ("Personalisation consent", "declared"),
}

METADATA = {"data_source": "source", "signal_confidence": "confidence",
            "signal_timestamp": "timestamp", "signal_status": "status"}


def empty_catalog(as_of, blocked=False):
    return {key: {"label": label, "value": None, "source": source, "confidence": None,
                  "confidence_kind": "not_available", "timestamp": str(as_of), "status": "unknown",
                  "availability": "blocked" if blocked else "unavailable", "evidence": [],
                  "limitations": ["Personalisation is not authorised." if blocked else "Insufficient source data or history."]}
            for key, (label, source) in SIGNALS.items()}


def set_signal(catalog, key, value, *, source=None, confidence=1.0, status="observed", timestamp=None, evidence=None, limitations=None):
    item = catalog[key]
    item.update(value=value, source=source or item["source"], confidence=confidence, status=status,
                confidence_kind="heuristic_not_calibrated" if confidence < 1 else "input_assertion_or_exact_calculation",
                timestamp=str(timestamp or item["timestamp"]), availability="available",
                evidence=evidence or [], limitations=limitations or [])
    return item
