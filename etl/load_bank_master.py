#!/usr/bin/env python3
"""โหลด Bank Master.csv -> ตาราง payin_bank_master (รายการบัญชีกลางทั้งหมด).

ใช้เป็น fallback: สาขาที่ไม่มีบัญชีผูกใน payin_branch_bank
ให้พนักงานเลือกบัญชีนำฝากจากรายการนี้แทน.

    python etl/load_bank_master.py [path/to/bank_master.csv]

ปลอดภัยถ้ารันซ้ำ (INSERT ... ON DUPLICATE KEY UPDATE).
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from app import db  # noqa: E402

DEFAULT_CSV = os.path.join(os.path.dirname(HERE), "data", "bank_master.csv")


def run(csv_path):
    rows = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f)):
            code = (r.get("Bank account") or "").strip()
            if not code:
                continue
            db.execute(
                """INSERT INTO payin_bank_master
                   (account_code, bank_group, bank_name, account_number, ledger_account, sort_order, active)
                   VALUES (%s,%s,%s,%s,%s,%s,1)
                   ON DUPLICATE KEY UPDATE
                     bank_group=VALUES(bank_group), bank_name=VALUES(bank_name),
                     account_number=VALUES(account_number), ledger_account=VALUES(ledger_account),
                     sort_order=VALUES(sort_order), active=1""",
                (code,
                 (r.get("Bank groups") or "").strip() or None,
                 (r.get("Bank name") or "").strip() or None,
                 (r.get("Bank account number") or "").strip() or None,
                 (r.get("Ledger account") or "").strip() or None,
                 i),
            )
            rows += 1
    return rows


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    n = run(path)
    total = db.query_one("SELECT COUNT(*) c FROM payin_bank_master")["c"]
    print(f"โหลด {n} แถวจาก {path} -> payin_bank_master (รวมในตาราง {total} บัญชี)")
