"""Curated source records, not offers or partner API integrations."""
import json
import os
from pathlib import Path

from ..life_schemas import ServiceRecord
from .banking import today

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "ecosystem.json"


def catalogue(path=None):
    source = Path(path or os.getenv("LIFEFLOW_CATALOGUE_PATH", str(DEFAULT_PATH)))
    values = json.loads(source.read_text(encoding="utf-8"))
    records = [ServiceRecord.model_validate(v).model_dump(mode="json") for v in values]
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("Duplicate catalogue identifiers")
    return records


def relevant_services(items, preferences):
    categories = {g["category"] for g in items if g["status"] == "active"}
    categories -= set(preferences.protected_categories)
    if preferences.allowed_categories:
        categories &= set(preferences.allowed_categories)
    return [s for s in catalogue() if s["verification_status"] == "official_source"
            and s["last_verified_at"] <= str(today())
            and categories.intersection(s["supported_goal_categories"])
            and not (preferences.financing_preference == "saving" and s["id"] == "kbc_home_loan_information")
            and not (s["ecosystem"] == "Investing" and
                     (not preferences.budgets or any(b.savings_pool is None or b.savings_pool < b.emergency_reserve
                         or b.monthly_capacity is None or b.monthly_capacity <= 0 for b in preferences.budgets)))]


def import_catalogue(source, destination):
    """Operator-only local import. JSON or XLSX with explicit schema column names."""
    source = Path(source)
    if source.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(source, read_only=True, data_only=True)
        rows = iter(book.active.values)
        columns = next(rows)
        values = []
        for cells in rows:
            if not any(cells):
                continue
            item = {k: v for k, v in zip(columns, cells) if k and v is not None}
            for field in ("supported_goal_categories", "required_information"):
                item[field] = json.loads(item.get(field, "[]"))
            # A workbook mention never asserts that verification happened.
            item.update(verification_status="catalogue_only", verified_url=None,
                        last_verified_at=None, integration_status="not_connected", requires_customer_action=True)
            values.append(item)
        book.close()
    else:
        values = json.loads(source.read_text(encoding="utf-8"))
    validated = [ServiceRecord.model_validate(v).model_dump(mode="json") for v in values]
    if len({r["id"] for r in validated}) != len(validated):
        raise ValueError("Duplicate catalogue identifiers")
    Path(destination).write_text(json.dumps(validated, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validate/import catalogue; does not contact any partner.")
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    import_catalogue(args.source, args.destination)
