"""
Audit raw age values from raw_scrapes to understand what PetFinder actually sends.

Run from the project root:
    python3 scripts/audit_age_values.py

Reads raw_scrapes.raw_json for every row and collects every distinct value
PetFinder places in physical.age.value and physical.age.rangeLabel.
Prints a ranked frequency table so you can validate (or fix) _normalize_age().
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Allow imports from the project root (config, models, db)
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.connection import Session
from models.dog import RawScrape


def _extract_age_fields(raw_json: dict) -> tuple[str | None, str | None]:
    """Pull (value, rangeLabel) from physical.age — both may be absent."""
    physical: dict = raw_json.get("physical") or {}
    age_obj: dict = physical.get("age") or {}
    return age_obj.get("value"), age_obj.get("rangeLabel")


def audit_age_values() -> None:
    value_counts: Counter[str] = Counter()
    range_label_counts: Counter[str] = Counter()
    missing = 0
    total = 0

    with Session() as session:
        rows: list[RawScrape] = session.query(RawScrape).all()
        total = len(rows)

        for row in rows:
            value, range_label = _extract_age_fields(row.raw_json)

            if value is None and range_label is None:
                missing += 1
            if value is not None:
                value_counts[value] += 1
            if range_label is not None:
                range_label_counts[range_label] += 1

    print(f"\nTotal raw_scrapes rows: {total}")
    print(f"Rows with no age data at all: {missing}\n")

    print("── physical.age.value (ranked by frequency) ──────────────────")
    if value_counts:
        for val, count in value_counts.most_common():
            pct = count / total * 100
            print(f"  {val!r:<20}  {count:>4}  ({pct:.1f}%)")
    else:
        print("  (none found)")

    print("\n── physical.age.rangeLabel (ranked by frequency) ─────────────")
    if range_label_counts:
        for val, count in range_label_counts.most_common():
            pct = count / total * 100
            print(f"  {val!r:<30}  {count:>4}  ({pct:.1f}%)")
    else:
        print("  (none found)")

    print()


if __name__ == "__main__":
    audit_age_values()