# coding: utf-8
"""Universe CSV loader with schema validation (SPEC v0.2 §2.5, 建议 #3).

Schema:
  - First column MUST be 'code' (raises ValueError if not).
  - 'code' must match ^\\d{6}\\.(SZ|SH)$; invalid rows are dropped + warned + recorded.
  - 'enabled' optional; defaults true; values in ('false','0','no','') treated as false.
  - Duplicate codes: first occurrence kept, later duplicates warn + skipped.
  - Empty universe (no valid rows) raises ValueError.
  - UTF-8 BOM tolerated via encoding='utf-8-sig'.
"""
import csv
import re
import logging

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^\d{6}\.(SZ|SH)$")


def _to_bool(v):
    if v is None:
        return True
    return str(v).strip().lower() not in ("false", "0", "no", "")


def load_universe(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or reader.fieldnames[0].lstrip("﻿") != "code":
            raise ValueError("universe CSV first column must be 'code'")
        rows = list(reader)

    seen = set()
    codes = []
    records = []
    dropped = []
    for row in rows:
        code = (row.get("code") or "").strip()
        if not CODE_RE.match(code):
            dropped.append(code)
            log.warning("universe: drop invalid code %r", code)
            continue
        if not _to_bool(row.get("enabled", "true")):
            continue
        if code in seen:
            log.warning("universe: duplicate code %s skipped", code)
            continue
        seen.add(code)
        codes.append(code)
        records.append({
            "code":    code,
            "name":    (row.get("name") or "").strip(),
            "sector":  (row.get("sector") or "").strip(),
            "enabled": True,
        })

    if not codes:
        raise ValueError("universe is empty")

    return {"codes": codes, "records": records, "dropped_codes": dropped}
