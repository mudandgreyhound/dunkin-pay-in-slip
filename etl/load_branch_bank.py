#!/usr/bin/env python3
"""
โหลด Branch Master (บัญชีธนาคารที่แต่ละสาขาต้องนำฝาก) เข้าตาราง payin_branch_bank

ใช้:  ./venv/bin/python etl/load_branch_bank.py "/path/to/Branch Master.csv"

คอลัมน์ CSV ที่คาดหวัง (มี header):
  CODE, DM, CODE AR, NAME BRANCH, TRANSFER TYPE, BANK NAME, BANK ACCOUNT, OFFSET ACCOUNT

- รับเฉพาะแถวที่ CODE เป็นตัวเลข 4 หลัก
- INSERT ... ON DUPLICATE KEY UPDATE (รันซ้ำได้ ปลอดภัย)
หมายเหตุ: ไฟล์ CSV มีเลขบัญชีจริง อย่า commit ขึ้น git (ถูก .gitignore ไว้)
"""
import os
import sys
import csv
import re
import pymysql
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))


def main(csv_path):
    rows, skipped = [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if not r or not r[0].strip() or r[0].strip().upper() == "CODE":
                continue
            code = r[0].strip()
            if not re.fullmatch(r"\d{4}", code):
                skipped.append(code)
                continue
            r = (r + [""] * 8)[:8]
            dm, ar, name, ttype, bank, acct, offset = [x.strip() for x in r[1:8]]
            rows.append((code, dm, ar, name, ttype, bank, acct, offset))

    conn = pymysql.connect(
        host=os.environ.get("DB_HOST", "10.8.1.88"),
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "GD_Dashboard"),
        charset="utf8mb4", autocommit=True,
    )
    sql = """INSERT INTO payin_branch_bank
      (shop_code,dm,ar_code,name_branch,transfer_type,bank_name,bank_account,offset_account)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
      ON DUPLICATE KEY UPDATE dm=VALUES(dm),ar_code=VALUES(ar_code),
        name_branch=VALUES(name_branch),transfer_type=VALUES(transfer_type),
        bank_name=VALUES(bank_name),bank_account=VALUES(bank_account),
        offset_account=VALUES(offset_account)"""
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, row)
        cur.execute("SELECT COUNT(*) FROM payin_branch_bank")
        total = cur.fetchone()[0]
    conn.close()
    print(f"loaded {len(rows)} lines ({len(set(r[0] for r in rows))} unique codes); "
          f"skipped={skipped}; table total={total}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: load_branch_bank.py "/path/to/Branch Master.csv"')
    main(sys.argv[1])
