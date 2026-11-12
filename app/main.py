"""
dunkin-pay-in-slip — FastAPI (พอร์ต 8608)
  ล็อกอินด้วยรหัสร้าน (GDxxxx / xxxx) -> ถ่ายรูป Day End + ใบนำฝาก
  -> OCR (tesseract) ดึงยอดเงิน -> บันทึก location + ระยะห่างจากร้าน -> เก็บลง DB

รัน dev :  ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8608 --reload
"""
import os
import json
import datetime as dt
from typing import List

from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import db, auth, ocr
from .geo import distance_info

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(ROOT, "static")
UPLOAD_DIR = os.path.join(ROOT, "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED = os.getenv(
    "ALLOWED_ORIGINS",
    "https://dunkin-pay-in-slip.mobile1234.site,http://localhost:8608",
).split(",")

app = FastAPI(title="dunkin-pay-in-slip")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- helpers ----------------

def current_shop_code(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    return auth.read_token(token)


def require_shop(request: Request):
    code = current_shop_code(request)
    if not code:
        raise HTTPException(status_code=401, detail="กรุณาเข้าสู่ระบบ")
    return code


def _f(v):
    """แปลง form value -> float หรือ None"""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def _loc_for(shop_code, lat, lng):
    loc = db.get_shop_location(shop_code)
    return distance_info(lat, lng, loc)


# ---------------- health & static ----------------

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "dunkin-pay-in-slip",
        "port": int(os.getenv("APP_PORT", "8608")),
        "ocr_available": ocr.available(),
    }


# ---------------- auth ----------------

@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    shop = auth.authenticate(username, password)
    if not shop:
        raise HTTPException(status_code=401, detail="รหัสร้านหรือรหัสผ่านไม่ถูกต้อง")
    token = auth.make_token(shop["shop_code"])
    resp = JSONResponse({"ok": True, "shop": shop})
    resp.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=auth.MAX_AGE, httponly=True, samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "1") == "1",
        path="/",
    )
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@app.get("/api/me")
def me(request: Request):
    code = current_shop_code(request)
    if not code:
        return JSONResponse({"authenticated": False}, status_code=200)
    shop = db.get_shop(code)
    loc = db.get_shop_location(code)
    bank = db.get_branch_bank(code)
    return {
        "authenticated": True,
        "shop": shop,
        "has_location": bool(loc and loc.get("lat") is not None),
        "radius_m": int(loc["radius_m"]) if loc else None,
        "bank": bank,              # บัญชีที่สาขานี้ต้องนำฝาก (หรือ None ถ้าไม่มีใน Branch Master)
        "ocr_available": ocr.available(),
    }


@app.get("/api/bank-master")
def bank_master(request: Request):
    """รายการบัญชีกลางทั้งหมด (ให้สาขาที่ไม่มีบัญชีผูกเลือกนำฝากแทน)"""
    require_shop(request)
    return {"ok": True, "accounts": db.list_bank_master()}


# ---------------- OCR preview (ไม่บันทึก) ----------------

@app.post("/api/ocr")
async def ocr_preview(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("day_end"),
    lat: str = Form(None),
    lng: str = Form(None),
):
    shop_code = require_shop(request)
    data = await file.read()
    # OCR เป็นงาน CPU-bound (เรียก tesseract) -> รันใน threadpool
    # ไม่ให้บล็อก event loop (ไม่งั้น request อื่นค้างหมดทั้งเซิร์ฟเวอร์)
    parsed = await run_in_threadpool(
        ocr.analyze, data, "day_end" if kind == "day_end" else "deposit")
    loc = _loc_for(shop_code, _f(lat), _f(lng))
    return {"ok": True, "parsed": parsed, "location": loc}


# ---------------- submit (บันทึกจริง) ----------------

@app.post("/api/submit")
async def submit(
    request: Request,
    day_end_file: UploadFile = File(None),
    deposit_files: List[UploadFile] = File(default=[]),   # ใบนำฝากหลายใบ (7-11 จำกัด 10,000/ใบ)
    deposit_amounts: List[str] = Form(default=[]),        # ยอดต่อใบ เรียงตรงกับ deposit_files
    cash_sales: str = Form(None),
    deposit_total: str = Form(None),
    deposit_channel: str = Form(None),
    depositor_name: str = Form(None),
    depositor_phone: str = Form(None),
    bank_account_code: str = Form(None),
    biz_date: str = Form(None),
    deposit_date: str = Form(None),
    note: str = Form(None),
    lat: str = Form(None),
    lng: str = Form(None),
    accuracy: str = Form(None),
    captured_at: str = Form(None),
):
    shop_code = require_shop(request)
    cash = _f(cash_sales)
    dep = _f(deposit_total)
    lat_f, lng_f = _f(lat), _f(lng)
    loc = _loc_for(shop_code, lat_f, lng_f)

    diff = round(dep - cash, 2) if (cash is not None and dep is not None) else None

    # snapshot บัญชีธนาคารของสาขา (Branch Master) เก็บติดไปกับรายการเพื่อลงบัญชี
    bank = db.get_branch_bank(shop_code) or {}

    # Bank Master: ถ้าพนักงานเลือกบัญชีเอง (สาขาไม่มีบัญชีผูก หรือกด "ใช้บัญชีอื่น")
    # -> ใช้บัญชีที่เลือกแทน (override snapshot ให้ตรงกับที่นำฝากจริง)
    code = (bank_account_code or "").strip()
    if code:
        bm = db.get_bank_master(code)
        if bm:
            bank = {
                "ar_code": None,
                "bank_name": bm.get("bank_group"),        # BBL/KBANK/SCB... (ให้เข้ากับ branch_bank)
                "bank_account": bm.get("account_number"),
                "offset_account": bm.get("account_code"),
            }
            note_tag = f"เลือกบัญชีเอง: {bm.get('bank_group') or ''} {bm.get('account_code')} {bm.get('account_number') or ''}".strip()
            note = ((note + " | ") if note else "") + note_tag

    # ตรวจ case ซ้ำ: ถ้าสาขานี้แจ้งยอด "วันที่ยอดขาย" เดียวกันไว้แล้ว
    # -> ถือว่าเป็นการแก้ไข/แจ้งซ้ำ: อันเก่า superseded, อันใหม่เป็น revision ถัดไป (ล่าสุดเป็นหลัก)
    revision = 1
    superseded_ids = []
    if biz_date:
        prev = db.query(
            "SELECT id, revision FROM payin_submission WHERE shop_code=%s AND biz_date=%s AND superseded=0",
            (shop_code, biz_date))
        if prev:
            revision = max(p["revision"] for p in prev) + 1
            superseded_ids = [p["id"] for p in prev]
            for pid in superseded_ids:
                db.execute("UPDATE payin_submission SET superseded=1 WHERE id=%s", (pid,))

    # 1) insert submission
    sub_id = db.execute(
        """INSERT INTO payin_submission
           (shop_code, biz_date, deposit_date, cash_sales, deposit_total, diff, deposit_channel,
            ar_code, bank_name, bank_account, offset_account,
            depositor_name, depositor_phone, revision, superseded,
            gps_lat, gps_lng, distance_m, within_radius, status, note, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,'submitted',%s,%s)""",
        (shop_code, biz_date or None, deposit_date or None, cash, dep, diff, deposit_channel or None,
         bank.get("ar_code"), bank.get("bank_name"), bank.get("bank_account"),
         bank.get("offset_account"),
         depositor_name or None, depositor_phone or None, revision,
         lat_f, lng_f, loc["distance_m"], loc["within_radius"], note or None,
         "GD" + shop_code),
    )

    # 2) save images + OCR text
    day_dir = os.path.join(UPLOAD_DIR, shop_code, (biz_date or dt.date.today().isoformat()))
    os.makedirs(day_dir, exist_ok=True)
    acc = _f(accuracy)
    acc_int = int(acc) if acc is not None else None
    cap = captured_at or None

    # รายการรูปที่จะบันทึก: Day End 1 รูป + ใบนำฝากได้หลายใบ (seq ใช้กันชื่อไฟล์ชน)
    jobs = []
    if day_end_file is not None and (day_end_file.filename or ""):
        jobs.append((day_end_file, "day_end", cash, 0))
    for i, up in enumerate(deposit_files or []):
        if up is None or not (up.filename or ""):
            continue
        amt = _f(deposit_amounts[i]) if i < len(deposit_amounts) else None
        jobs.append((up, "deposit", amt, i + 1))

    saved = []
    for up, kind, amount, seq in jobs:
        data = await up.read()
        ts = dt.datetime.now().strftime("%H%M%S")
        ext = os.path.splitext(up.filename or "")[1].lower() or ".jpg"
        suffix = f"_{seq}" if kind == "deposit" else ""
        fname = f"{ts}_{kind}{suffix}{ext}"
        fpath = os.path.join(day_dir, fname)
        with open(fpath, "wb") as f:
            f.write(data)
        parsed = await run_in_threadpool(ocr.analyze, data, kind)
        # ยอดต่อใบ: ใช้ที่ผู้ใช้ยืนยันมา ถ้าไม่มีค่อย fallback ไปที่ OCR อ่านได้
        amt_final = amount
        if amt_final is None:
            amt_final = parsed.get("deposit_amount") if kind == "deposit" else parsed.get("cash_sales")
        db.execute(
            """INSERT INTO payin_image
               (submission_id, shop_code, kind, file_path, amount, ocr_text, ocr_json,
                gps_lat, gps_lng, gps_accuracy_m, distance_m, within_radius, captured_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sub_id, shop_code, kind, fpath, amt_final,
             parsed.get("text"), json.dumps(parsed, ensure_ascii=False),
             lat_f, lng_f, acc_int, loc["distance_m"], loc["within_radius"], cap),
        )
        saved.append(kind)

    return {"ok": True, "submission_id": sub_id, "diff": diff,
            "location": loc, "saved_images": saved,
            "deposit_slips": sum(1 for s in saved if s == "deposit"),
            "revision": revision, "superseded": len(superseded_ids)}


# ---------------- ประวัติของร้าน ----------------

@app.get("/api/my-submission")
def my_submission(request: Request, biz_date: str):
    """คืนรายการที่สาขานี้แจ้งไว้แล้วสำหรับ 'วันที่ยอดขาย' นี้ (ไว้เตือนก่อนแจ้งซ้ำ) หรือ null"""
    shop_code = require_shop(request)
    row = db.query_one(
        """SELECT id, deposit_total, revision, created_at, depositor_name
           FROM payin_submission
           WHERE shop_code=%s AND biz_date=%s AND superseded=0
           ORDER BY id DESC LIMIT 1""",
        (shop_code, biz_date))
    if not row:
        return {"exists": False}
    return {"exists": True,
            "deposit_total": float(row["deposit_total"]) if row["deposit_total"] is not None else None,
            "revision": row["revision"],
            "depositor_name": row.get("depositor_name"),
            "at": row["created_at"].isoformat(sep=" ", timespec="minutes") if row.get("created_at") else None}


@app.get("/api/submissions")
def submissions(request: Request, limit: int = 30):
    shop_code = require_shop(request)
    limit = max(1, min(limit, 200))
    rows = db.query(
        """SELECT id, biz_date, cash_sales, deposit_total, diff, deposit_channel,
                  distance_m, within_radius, status, created_at
           FROM payin_submission
           WHERE shop_code=%s
           ORDER BY created_at DESC
           LIMIT %s""",
        (shop_code, limit),
    )
    for r in rows:
        if r.get("biz_date"):
            r["biz_date"] = r["biz_date"].isoformat()
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat(sep=" ", timespec="minutes")
    return {"ok": True, "shop_code": shop_code, "rows": rows}


# ================= ผู้ตรวจสอบ (admin / back-office) =================

def current_admin(request: Request):
    return auth.read_admin_token(request.cookies.get(auth.ADMIN_COOKIE))


def require_admin(request: Request):
    u = current_admin(request)
    if not u:
        raise HTTPException(status_code=401, detail="ต้องเข้าสู่ระบบผู้ตรวจสอบ")
    return u


def current_reviewer(request: Request):
    """ผู้เข้าดูหน้า /review: admin (เห็นทุกสาขา) หรือ DM (เห็นเฉพาะสาขาที่ดูแล)"""
    u = current_admin(request)
    if u:
        return {"kind": "admin", "name": u, "dm_name": None, "display_name": u}
    uname = auth.read_dm_token(request.cookies.get(auth.DM_COOKIE))
    if uname:
        dmu = db.get_dm_user(uname)
        if dmu and dmu.get("active"):
            return {"kind": "dm", "name": dmu["username"], "dm_name": dmu["dm_name"],
                    "display_name": dmu.get("display_name") or dmu["dm_name"]}
    return None


def require_reviewer(request: Request):
    r = current_reviewer(request)
    if not r:
        raise HTTPException(status_code=401, detail="ต้องเข้าสู่ระบบผู้ตรวจสอบ")
    return r


def _ensure_scope(reviewer, shop_code):
    """DM เข้าถึงได้เฉพาะสาขาที่ dim_shop.dm ตรงกับ dm_name ของตัวเอง; admin เข้าได้ทุกสาขา"""
    if reviewer["kind"] == "admin":
        return
    shop = db.get_shop(shop_code)
    if not shop or (shop.get("dm") or None) != reviewer["dm_name"]:
        raise HTTPException(status_code=403, detail="ไม่มีสิทธิ์เข้าถึงสาขานี้")


@app.post("/api/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    u = auth.authenticate_admin(body.get("username"), body.get("password"))
    if not u:
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    resp = JSONResponse({"ok": True, "user": u})
    resp.set_cookie(auth.ADMIN_COOKIE, auth.make_admin_token(u),
                    max_age=auth.MAX_AGE, httponly=True, samesite="lax",
                    secure=os.getenv("COOKIE_SECURE", "1") == "1", path="/")
    return resp


@app.post("/api/admin/logout")
def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.ADMIN_COOKIE, path="/")
    return resp


@app.get("/api/admin/me")
def admin_me(request: Request):
    u = current_admin(request)
    return {"authenticated": bool(u), "user": u}


# ---------------- DM login (ผู้จัดการเขต — เห็นเฉพาะสาขาที่ดูแล) ----------------

@app.post("/api/dm/login")
async def dm_login(request: Request):
    body = await request.json()
    u = auth.authenticate_dm(body.get("username"), body.get("password"))
    if not u:
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง หรือบัญชีถูกระงับ")
    resp = JSONResponse({"ok": True, "dm": {
        "username": u["username"], "dm_name": u["dm_name"],
        "display_name": u.get("display_name") or u["dm_name"]}})
    resp.set_cookie(auth.DM_COOKIE, auth.make_dm_token(u["username"]),
                    max_age=auth.MAX_AGE, httponly=True, samesite="lax",
                    secure=os.getenv("COOKIE_SECURE", "1") == "1", path="/")
    return resp


@app.post("/api/dm/logout")
def dm_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.DM_COOKIE, path="/")
    return resp


# รวม admin + DM: หน้า /review ใช้ตัวนี้ตรวจว่าใครล็อกอินอยู่
@app.get("/api/review/me")
def review_me(request: Request):
    r = current_reviewer(request)
    if not r:
        return {"authenticated": False}
    return {"authenticated": True, "kind": r["kind"], "name": r["name"],
            "dm_name": r["dm_name"], "display_name": r["display_name"]}


@app.get("/api/review/dm-list")
def review_dm_list(request: Request):
    """รายชื่อ DM สำหรับ dropdown (เฉพาะ admin; DM ไม่ต้องใช้เพราะล็อกสาขาตัวเองแล้ว)"""
    rv = require_reviewer(request)
    if rv["kind"] != "admin":
        return {"ok": True, "dms": []}
    return {"ok": True, "dms": db.list_active_dms()}


def _category(row):
    """จัดสถานะรายการ: not_submitted / no_cash / verified / pending"""
    if row.get("id") is None:
        return "not_submitted"
    if row.get("status") == "verified":
        return "verified"
    if row.get("status") == "need_info":
        return "need_info"
    dep = row.get("deposit_total")
    if dep is not None and float(dep) == 0:
        return "no_cash"
    return "pending"


@app.get("/api/review/summary")
def review_summary(request: Request, date: str = None, dm: str = None):
    rv = require_reviewer(request)
    # หน้า "ตรวจสอบยอดฝากรายวัน" ยึดวันที่ฝาก (deposit_date); เผื่อว่างใช้ biz_date แทน
    review_date = date or dt.date.today().isoformat()

    # ขอบเขตสาขาตาม DM (dim_shop.dm) — DM เห็นเฉพาะของตัวเอง; admin เลือกกรองได้ (ว่าง=ทุก DM)
    if rv["kind"] == "dm":
        dm_filter = rv["dm_name"]
    else:
        dm_filter = (dm or "").strip() or None

    params = [review_date, review_date]
    dm_where = ""
    if dm_filter:
        dm_where = "WHERE ds.dm = %s"
        params.append(dm_filter)

    rows = db.query(
        """
        SELECT base.shop_code,
               COALESCE(ds.shop_name, b.name_branch) AS shop_name,
               ds.dm, b.bank_name, b.bank_account,
               sub.id, sub.cash_sales, sub.deposit_total, sub.diff, sub.deposit_date, sub.biz_date,
               sub.deposit_channel, sub.depositor_name, sub.depositor_phone,
               sub.status, sub.within_radius, sub.created_at, sub.revision,
               di.cap AS dep_captured,
               sub.verified_by, sub.verified_at
        FROM (
            SELECT shop_code FROM payin_branch_bank
            UNION
            SELECT shop_code FROM payin_submission WHERE COALESCE(deposit_date, biz_date) = %s
        ) base
        LEFT JOIN payin_branch_bank b ON b.shop_code = base.shop_code
        LEFT JOIN dim_shop ds ON ds.shop_code = base.shop_code
        LEFT JOIN (
            SELECT t.* FROM payin_submission t
            JOIN (SELECT shop_code, MAX(id) mid FROM payin_submission
                  WHERE COALESCE(deposit_date, biz_date) = %s GROUP BY shop_code) m ON m.mid = t.id
        ) sub ON sub.shop_code = base.shop_code
        LEFT JOIN (
            SELECT submission_id, MAX(captured_at) cap FROM payin_image
            WHERE kind = 'deposit' GROUP BY submission_id
        ) di ON di.submission_id = sub.id
        """ + dm_where + """
        ORDER BY base.shop_code
        """,
        tuple(params),
    )
    counts = {"all": 0, "pending": 0, "verified": 0, "not_submitted": 0, "no_cash": 0, "need_info": 0}
    out = []
    for r in rows:
        cat = _category(r)
        counts["all"] += 1
        counts[cat] = counts.get(cat, 0) + 1
        dep_dt = r.get("dep_captured") or r.get("created_at")
        out.append({
            "shop_code": r["shop_code"], "shop_name": r["shop_name"], "dm": r.get("dm"),
            "bank_name": r.get("bank_name"), "bank_account": r.get("bank_account"),
            "submission_id": r.get("id"),
            "cash_sales": float(r["cash_sales"]) if r.get("cash_sales") is not None else None,
            "deposit_total": float(r["deposit_total"]) if r.get("deposit_total") is not None else None,
            "diff": float(r["diff"]) if r.get("diff") is not None else None,
            "deposit_dt": dep_dt.isoformat(sep=" ", timespec="minutes") if dep_dt else None,
            "submit_dt": r["created_at"].isoformat(sep=" ", timespec="minutes") if r.get("created_at") else None,
            "depositor_name": r.get("depositor_name"),
            "category": cat,
            "within_radius": r.get("within_radius"),
            "verified_by": r.get("verified_by"),
            "revision": r.get("revision"),
        })
    return {"ok": True, "date": review_date, "counts": counts, "rows": out}


@app.get("/api/review/detail/{sid}")
def review_detail(request: Request, sid: int):
    rv = require_reviewer(request)
    s = db.query_one(
        """SELECT s.*, COALESCE(ds.shop_name, bb.name_branch) AS shop_name
           FROM payin_submission s
           LEFT JOIN dim_shop ds ON ds.shop_code = s.shop_code
           LEFT JOIN payin_branch_bank bb ON bb.shop_code = s.shop_code
           WHERE s.id = %s""", (sid,))
    if not s:
        raise HTTPException(status_code=404, detail="ไม่พบรายการ")
    _ensure_scope(rv, s["shop_code"])
    imgs = db.query(
        "SELECT id, kind, amount, captured_at FROM payin_image WHERE submission_id=%s ORDER BY kind DESC, id ASC", (sid,))
    dep_img = next((i for i in imgs if i["kind"] == "deposit"), None)
    s["captured_at"] = dep_img["captured_at"] if dep_img and dep_img.get("captured_at") else None
    # ประวัติการแจ้งซ้ำ/แก้ไข (รายการอื่นของสาขา+วันที่ยอดขายเดียวกัน)
    hist = []
    if s.get("biz_date"):
        hist = db.query(
            """SELECT id, revision, deposit_total, created_at, depositor_name, superseded
               FROM payin_submission
               WHERE shop_code=%s AND biz_date=%s AND id<>%s
               ORDER BY id DESC""",
            (s["shop_code"], s["biz_date"], sid))
    s["history"] = [{"id": h["id"], "revision": h["revision"],
                     "deposit_total": float(h["deposit_total"]) if h["deposit_total"] is not None else None,
                     "depositor_name": h.get("depositor_name"),
                     "at": h["created_at"].isoformat(sep=" ", timespec="minutes") if h.get("created_at") else None}
                    for h in hist]
    for k in ("biz_date", "deposit_date"):
        if s.get(k):
            s[k] = s[k].isoformat()
    for k in ("created_at", "captured_at", "verified_at"):
        if s.get(k):
            s[k] = s[k].isoformat(sep=" ", timespec="minutes")
    for n in ("cash_sales", "deposit_total", "diff", "gps_lat", "gps_lng"):
        if s.get(n) is not None:
            s[n] = float(s[n])
    s["images"] = [{"id": i["id"], "kind": i["kind"],
                    "amount": float(i["amount"]) if i.get("amount") is not None else None,
                    "captured_at": i["captured_at"].isoformat(sep=" ", timespec="minutes") if i.get("captured_at") else None}
                   for i in imgs]
    return {"ok": True, "submission": s}


def _submission_shop(sid):
    row = db.query_one("SELECT shop_code FROM payin_submission WHERE id=%s", (sid,))
    if not row:
        raise HTTPException(status_code=404, detail="ไม่พบรายการ")
    return row["shop_code"]


@app.post("/api/review/verify/{sid}")
def review_verify(request: Request, sid: int):
    rv = require_reviewer(request)
    _ensure_scope(rv, _submission_shop(sid))
    db.execute("UPDATE payin_submission SET status='verified', verified_by=%s, verified_at=NOW() WHERE id=%s",
               (rv["name"], sid))
    return {"ok": True}


@app.post("/api/review/request-info/{sid}")
async def review_request_info(request: Request, sid: int):
    rv = require_reviewer(request)
    _ensure_scope(rv, _submission_shop(sid))
    body = await request.json()
    note = (body.get("note") or "").strip() or None
    db.execute("UPDATE payin_submission SET status='need_info', review_note=%s WHERE id=%s", (note, sid))
    return {"ok": True}


@app.get("/api/review/image/{image_id}")
def review_image(request: Request, image_id: int):
    rv = require_reviewer(request)
    img = db.query_one("SELECT file_path, shop_code FROM payin_image WHERE id=%s", (image_id,))
    if not img or not img.get("file_path") or not os.path.exists(img["file_path"]):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์รูป")
    _ensure_scope(rv, img["shop_code"])
    return FileResponse(img["file_path"])


@app.get("/review")
def review_page():
    path = os.path.join(STATIC, "review.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="not found")


# ---------------- หน้าเว็บ ----------------

@app.get("/")
def index():
    path = os.path.join(STATIC, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"ok": True, "service": "dunkin-pay-in-slip"}


# เสิร์ฟไฟล์ static อื่น ๆ (app.js ฯลฯ)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
