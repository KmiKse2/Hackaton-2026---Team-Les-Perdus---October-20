from datetime import date, timedelta

from sqlalchemy import select

from ..context_schemas import ContextImport, PersonalizationConsent
from ..models import Customer, CustomerContext
from .banking import today


def personalization_allowed(db, customer_id):
    row = db.get(CustomerContext, customer_id)
    if row is None:
        return False
    consent = row.personalization
    return (consent.get("value") is True and date.fromisoformat(consent["observed_at"]) <= today()
            and (not consent.get("valid_until") or date.fromisoformat(consent["valid_until"]) >= today()))


def context_row(db, customer_id):
    row = db.get(CustomerContext, customer_id)
    if row is None:
        row = CustomerContext(customer_id=customer_id, attributes={}, products=None, usage=None,
                              personalization={"value": False, "source": "declared", "observed_at": str(today())})
        db.add(row)
        db.flush()
    return row


def update_context(db, customer_id, payload: ContextImport):
    row = context_row(db, customer_id)
    if "attributes" in payload.model_fields_set:
        if payload.attributes is None:
            row.attributes = {}
        else:
            values = dict(row.attributes)
            for key in payload.attributes.model_fields_set:
                fact = getattr(payload.attributes, key)
                if fact is None:
                    values.pop(key, None)
                else:
                    values[key] = fact.model_dump(mode="json")
            row.attributes = values
    for key in ("products", "usage"):
        if key in payload.model_fields_set:
            value = getattr(payload, key)
            setattr(row, key, value.model_dump(mode="json") if value is not None else None)
    db.flush()


def update_personalization(db, customer_id, payload: PersonalizationConsent):
    row = context_row(db, customer_id)
    row.personalization = payload.model_dump(mode="json")
    db.flush()


def bootstrap_context(db):
    """Only the three named synthetic personas get explicit example declarations."""
    examples = {
        1: ("Thomas", "2004-05-12", "student", "single", 0, "student", "IT", "build_emergency_fund"),
        2: ("Sophie", "1993-02-20", "employed", "married", 1, "nurse", "healthcare", "save_for_house"),
        3: ("Marc", "1968-11-08", "self_employed", "divorced", 0, "consultant", "finance", "travel"),
    }
    for customer in db.scalars(select(Customer)):
        if db.get(CustomerContext, customer.id):
            continue
        example = examples.get(customer.id)
        if not example or customer.name != example[0]:
            context_row(db, customer.id)
            continue
        _, birth, work, household, dependents, occupation, industry, goal = example
        def fact(value, source="declared"):
            return {"value": value, "source": source, "observed_at": "2026-08-01", "valid_until": "2027-08-01"}
        attributes = {"date_of_birth": fact(birth, "kyc"), "employment_status": fact(work),
                      "household_status": fact(household), "dependents_count": fact(dependents),
                      "occupation": fact(occupation), "industry": fact(industry), "financial_goal": fact(goal),
                      "preferred_channel": fact("mobile")}
        db.add(CustomerContext(customer_id=customer.id, attributes=attributes, products=None, usage=None,
                              personalization={"value": True, "source": "declared", "observed_at": str(today()),
                                               "valid_until": str(today() + timedelta(days=90))}, synthetic=True))
    db.commit()
