"""
การเชื่อมต่อฐานข้อมูล GD_Dashboard สำหรับ dunkin-pay-in-slip

- อ่าน dim_shop / shop_location (ของเดิม — อ่านอย่างเดียว ไม่แก้)
- เขียน payin_submission / payin_image (ตารางใหม่ของโปรเจกต์นี้)
ใช้ user admin ซึ่งมีสิทธิ์เขียน; ระวังไม่ยุ่งกับตารางเดิมนอกจากอ่าน
"""
import os
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))

DB_HOST = os.environ.get("DB_HOST", "10.8.1.88")
DB_PORT = int(os.environ.get("DB_PORT", 3306))
DB_NAME = os.environ.get("DB_NAME", "GD_Dashboard")
DB_USER = os.environ.get("DB_USER", "admin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=5,
        read_timeout=30,
        write_timeout=10,
    )


def query(sql, params=None):
    """SELECT -> list[dict]"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    """INSERT/UPDATE -> lastrowid"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.lastrowid
    finally:
        conn.close()


# ---------- helpers เฉพาะงาน ----------

def get_shop(shop_code):
    return query_one(
        "SELECT shop_code, shop_name, dm, head FROM dim_shop WHERE shop_code=%s",
        (shop_code,),
    )


def get_shop_location(shop_code):
    return query_one(
        "SELECT shop_code, lat, lng, radius_m FROM shop_location WHERE shop_code=%s",
        (shop_code,),
    )


def get_branch_bank(shop_code):
    """บัญชีธนาคารที่สาขานี้ต้องนำฝาก (Branch Master)"""
    return query_one(
        """SELECT shop_code, dm, ar_code, name_branch, transfer_type,
                  bank_name, bank_account, offset_account
           FROM payin_branch_bank WHERE shop_code=%s""",
        (shop_code,),
    )


def list_bank_master():
    """รายการบัญชีกลางทั้งหมด (fallback ให้เลือกเมื่อสาขาไม่มีบัญชีผูก)"""
    return query(
        """SELECT account_code, bank_group, bank_name, account_number, ledger_account
           FROM payin_bank_master WHERE active=1
           ORDER BY bank_group, sort_order, account_code""",
    )


def get_bank_master(account_code):
    """บัญชีเดียวจาก Bank Master ตามรหัสบัญชี (account_code = offset_account)"""
    return query_one(
        """SELECT account_code, bank_group, bank_name, account_number, ledger_account
           FROM payin_bank_master WHERE account_code=%s""",
        (account_code,),
    )
