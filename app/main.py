"""
dunkin-pay-in-slip — FastAPI (พอร์ต 8608)
  ล็อกอินด้วยรหัสร้าน (GDxxxx / xxxx) -> ถ่ายรูป Day End + ใบนำฝาก
  -> OCR (tesseract) ดึงยอดเงิน -> บันทึก location + ระยะห่างจากร้าน -> เก็บลง DB

รัน dev :  ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8608 --reload
"""
import os
import json
import datetime as dt

from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    parsed = ocr.analyze(data, "day_end" if kind == "day_end" else "deposit")
    loc = _loc_for(shop_code, _f(lat), _f(lng))
    return {"ok": True, "parsed": parsed, "location": loc}


# ---------------- submit (บันทึกจริง) ----------------

@app.post("/api/submit")
async def submit(
    request: Request,
    day_end_file: UploadFile = File(None),
    deposit_file: UploadFile = File(None),
    cash_sales: str = Form(None),
    deposit_total: str = Form(None),
    deposit_channel: str = Form(None),
    depositor_name: str = Form(None),
    depositor_phone: str = Form(None),
    biz_date: str = Form(None),
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

    # 1) insert submission
    sub_id = db.execute(
        """INSERT INTO payin_submission
           (shop_code, biz_date, cash_sales, deposit_total, diff, deposit_channel,
            ar_code, bank_name, bank_account, offset_account,
            depositor_name, depositor_phone,
            gps_lat, gps_lng, distance_m, within_radius, status, note, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'submitted',%s,%s)""",
        (shop_code, biz_date or None, cash, dep, diff, deposit_channel or None,
         bank.get("ar_code"), bank.get("bank_name"), bank.get("bank_account"),
         bank.get("offset_account"),
         depositor_name or None, depositor_phone or None,
         lat_f, lng_f, loc["distance_m"], loc["within_radius"], note or None,
         "GD" + shop_code),
    )

    # 2) save images + OCR text
    day_dir = os.path.join(UPLOAD_DIR, shop_code, (biz_date or dt.date.today().isoformat()))
    os.makedirs(day_dir, exist_ok=True)
    acc = _f(accuracy)
    acc_int = int(acc) if acc is not None else None
    cap = captured_at or None

    saved = []
    for up, kind, amount in (
        (day_end_file, "day_end", cash),
        (deposit_file, "deposit", dep),
    ):
        if up is None:
            continue
        data = await up.read()
        ts = dt.datetime.now().strftime("%H%M%S")
        ext = os.path.splitext(up.filename or "")[1].lower() or ".jpg"
        fname = f"{ts}_{kind}{ext}"
        fpath = os.path.join(day_dir, fname)
        with open(fpath, "wb") as f:
            f.write(data)
        parsed = ocr.analyze(data, kind)
        db.execute(
            """INSERT INTO payin_image
               (submission_id, shop_code, kind, file_path, amount, ocr_text, ocr_json,
                gps_lat, gps_lng, gps_accuracy_m, distance_m, within_radius, captured_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sub_id, shop_code, kind, fpath, amount,
             parsed.get("text"), json.dumps(parsed, ensure_ascii=False),
             lat_f, lng_f, acc_int, loc["distance_m"], loc["within_radius"], cap),
        )
        saved.append(kind)

    return {"ok": True, "submission_id": sub_id, "diff": diff,
            "location": loc, "saved_images": saved}


# ---------------- ประวัติของร้าน ----------------

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


# ---------------- หน้าเว็บ ----------------

@app.get("/")
def index():
    path = os.path.join(STATIC, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"ok": True, "service": "dunkin-pay-in-slip"}


# เสิร์ฟไฟล์ static อื่น ๆ (app.js ฯลฯ)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
