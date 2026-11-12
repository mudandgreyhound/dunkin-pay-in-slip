#!/usr/bin/env python3
"""
จัดการบัญชีล็อกอินของ DM (ตาราง payin_dm_user) สำหรับหน้า /review
  DM แต่ละคนเห็นเฉพาะสาขาที่ dim_shop.dm = dm_name ของตัวเอง

ใช้ (รันจาก root ของโปรเจกต์):
  ./venv/bin/python etl/manage_dm_users.py seed            # สร้างบัญชีให้ DM ที่ยัง active และยังไม่มีบัญชี (สุ่มรหัส)
  ./venv/bin/python etl/manage_dm_users.py seed --reset    # สร้าง + รีเซ็ตรหัสของบัญชีเดิมทั้งหมดด้วย
  ./venv/bin/python etl/manage_dm_users.py list            # ดูรายชื่อบัญชี + จำนวนสาขาที่ดูแล
  ./venv/bin/python etl/manage_dm_users.py setpass <user> [รหัส]   # ตั้ง/รีเซ็ตรหัส (ไม่ใส่=สุ่มให้)
  ./venv/bin/python etl/manage_dm_users.py add <user> "<dm_name>" [รหัส]  # เพิ่มบัญชีเอง
  ./venv/bin/python etl/manage_dm_users.py activate <user>
  ./venv/bin/python etl/manage_dm_users.py deactivate <user>

หมายเหตุ: รหัสผ่านที่สุ่มจะแสดงครั้งเดียวตอนสร้าง (เก็บเฉพาะ hash) — คัดลอกไปแจก DM ได้เลย
"""
import os
import re
import sys
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import db, auth  # noqa: E402

# ชุดอักขระสำหรับรหัสสุ่ม — ตัดตัวที่อ่านสับสน (0/O/1/l/I)
_PW_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def gen_password(n=8):
    return "".join(secrets.choice(_PW_CHARS) for _ in range(n))


def username_for(dm_name, taken):
    """สร้าง username จากชื่อเล่นในวงเล็บ เช่น 'K.Suchitra(Kai)' -> 'Kai' (กันชนด้วยเลขต่อท้าย)"""
    m = re.search(r"\(([^)]+)\)", dm_name or "")
    base = m.group(1) if m else (dm_name or "")
    base = re.sub(r"[^A-Za-z0-9]", "", base) or "dm"
    cand = base
    i = 2
    low = {t.lower() for t in taken}
    while cand.lower() in low:
        cand = f"{base}{i}"
        i += 1
    return cand


def _existing():
    """{dm_name: username} ของบัญชีที่มีอยู่แล้ว + เซ็ต username ที่ถูกใช้"""
    rows = db.query("SELECT username, dm_name FROM payin_dm_user")
    by_dm = {r["dm_name"]: r["username"] for r in rows}
    return by_dm, {r["username"] for r in rows}


def _set_user(username, dm_name, display_name, password):
    db.execute(
        """INSERT INTO payin_dm_user (username, dm_name, display_name, password_hash, active)
           VALUES (%s,%s,%s,%s,1)
           ON DUPLICATE KEY UPDATE dm_name=VALUES(dm_name),
             display_name=VALUES(display_name), password_hash=VALUES(password_hash), active=1""",
        (username, dm_name, display_name, auth.hash_password(password)),
    )


def cmd_seed(reset=False):
    dms = db.list_active_dms()
    by_dm, taken = _existing()
    created, reset_done = [], []
    for dm in dms:
        if dm in by_dm:
            if reset:
                pw = gen_password()
                _set_user(by_dm[dm], dm, dm, pw)
                reset_done.append((by_dm[dm], dm, pw))
            continue
        user = username_for(dm, taken)
        taken.add(user)
        pw = gen_password()
        _set_user(user, dm, dm, pw)
        created.append((user, dm, pw))

    def show(title, items):
        if not items:
            return
        print(f"\n{title}")
        print(f"  {'USERNAME':<14} {'PASSWORD':<12} DM")
        for u, dm, pw in items:
            print(f"  {u:<14} {pw:<12} {dm}")

    show("สร้างบัญชีใหม่ (คัดลอกรหัสไปแจก DM):", created)
    show("รีเซ็ตรหัสบัญชีเดิม:", reset_done)
    print(f"\nสรุป: DM active ทั้งหมด {len(dms)} คน | สร้างใหม่ {len(created)} | รีเซ็ต {len(reset_done)} | "
          f"มีอยู่แล้ว {len(dms) - len(created)}")


def cmd_list():
    rows = db.query("""
        SELECT u.username, u.dm_name, u.display_name, u.active,
               (SELECT COUNT(*) FROM dim_shop d WHERE d.dm = u.dm_name) AS shops
        FROM payin_dm_user u ORDER BY u.active DESC, u.username""")
    if not rows:
        print("ยังไม่มีบัญชี DM — รัน seed ก่อน")
        return
    print(f"{'USERNAME':<14} {'ACTIVE':<7} {'SHOPS':<6} DM")
    for r in rows:
        print(f"{r['username']:<14} {('yes' if r['active'] else 'NO'):<7} {r['shops']:<6} {r['dm_name']}")
    print(f"\nรวม {len(rows)} บัญชี")


def cmd_setpass(username, password=None):
    u = db.get_dm_user(username)
    if not u:
        sys.exit(f"ไม่พบบัญชี: {username}")
    pw = password or gen_password()
    db.execute("UPDATE payin_dm_user SET password_hash=%s WHERE username=%s",
               (auth.hash_password(pw), username))
    print(f"ตั้งรหัสใหม่ให้ {username} แล้ว: {pw}")


def cmd_add(username, dm_name, password=None):
    if db.get_dm_user(username):
        sys.exit(f"มีบัญชี {username} อยู่แล้ว (ใช้ setpass เพื่อเปลี่ยนรหัส)")
    pw = password or gen_password()
    _set_user(username, dm_name, dm_name, pw)
    print(f"เพิ่มบัญชี {username} (DM: {dm_name}) รหัส: {pw}")
    n = db.query_one("SELECT COUNT(*) c FROM dim_shop WHERE dm=%s", (dm_name,))["c"]
    if n == 0:
        print(f"  ⚠ เตือน: ไม่พบสาขาที่ dim_shop.dm = '{dm_name}' — ตรวจชื่อ DM ให้ตรงตัว")


def cmd_set_active(username, active):
    if not db.get_dm_user(username):
        sys.exit(f"ไม่พบบัญชี: {username}")
    db.execute("UPDATE payin_dm_user SET active=%s WHERE username=%s", (1 if active else 0, username))
    print(f"{'เปิดใช้งาน' if active else 'ระงับ'}บัญชี {username} แล้ว")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd = argv[0]
    if cmd == "seed":
        cmd_seed(reset="--reset" in argv[1:])
    elif cmd == "list":
        cmd_list()
    elif cmd == "setpass":
        cmd_setpass(*argv[1:3])
    elif cmd == "add":
        if len(argv) < 3:
            sys.exit('usage: add <user> "<dm_name>" [password]')
        cmd_add(argv[1], argv[2], argv[3] if len(argv) > 3 else None)
    elif cmd == "activate":
        cmd_set_active(argv[1], True)
    elif cmd == "deactivate":
        cmd_set_active(argv[1], False)
    else:
        sys.exit(f"ไม่รู้จักคำสั่ง: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
