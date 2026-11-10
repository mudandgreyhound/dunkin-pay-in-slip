# dunkin-pay-in-slip

ระบบบันทึก **ใบนำฝากเงินสด** ของร้าน Dunkin' — FastAPI พอร์ต `8608`
Public: `https://dunkin-pay-in-slip.mobile1234.site` (ผ่าน cloudflared tunnel `mobile_dashboard`)

พนักงานล็อกอินด้วยรหัสร้าน → ถ่ายรูป **Day End Report** + **ใบนำฝาก** →
ระบบ OCR อ่านยอดเงิน + บันทึก **พิกัด GPS/ระยะห่างจากร้าน** → เก็บลงฐานข้อมูล

## ความสามารถ
1. **Login ด้วยรหัสร้าน** — Username `GDxxxx` / Password `xxxx` (ตรวจกับ `dim_shop`)
2. **บันทึก location ตอนถ่ายรูป** — คำนวณระยะห่างจากพิกัดร้าน (`shop_location`) ว่าอยู่ในรัศมีไหม / ห่างกี่กิโล
3. **OCR (Tesseract)**
   - Day End Report → ดึง **Cash Sales** = เงินสดที่ต้องนำฝาก
   - ใบนำฝาก (ธนาคาร / CP AXTRA / เซเว่น ฯลฯ) → ดึงยอดเงินฝาก + เดาช่องทาง
   - OCR เป็นตัว *ช่วยกรอก* — พนักงานยืนยัน/แก้ไขก่อนส่งเสมอ
4. **ฐานข้อมูล** `payin_submission`, `payin_image` (ใน `GD_Dashboard`, ไม่แตะตารางเดิม)

## โครงไฟล์
```
app/main.py     — FastAPI routes (login, me, ocr, submit, submissions)
app/db.py       — เชื่อม GD_Dashboard (อ่าน dim_shop/shop_location, เขียน payin_*)
app/auth.py     — ล็อกอิน + session cookie (itsdangerous)
app/ocr.py      — Tesseract wrapper + parser ยอดเงิน
app/geo.py      — Haversine ระยะห่างจากร้าน
app/schema.sql  — DDL ตาราง payin_* (CREATE IF NOT EXISTS)
static/index.html — หน้าเว็บมือถือ (login + ถ่ายรูป + OCR + ประวัติ)
data/uploads/<shop>/<date>/ — ไฟล์รูปที่อัปโหลด
```

## ฐานข้อมูล
- อ่าน: `dim_shop` (shop_code, shop_name, dm, head), `shop_location` (lat, lng, radius_m)
- เขียน: `payin_submission` (ยอด/ส่วนต่าง/พิกัด), `payin_image` (รูป + ocr_text + ocr_json)
- สร้างตาราง (รันซ้ำได้ ปลอดภัย):
```bash
./venv/bin/python - <<'PY'
import os,pymysql; from dotenv import load_dotenv; load_dotenv(".env")
raw=open("app/schema.sql").read()
sql="\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
c=pymysql.connect(host=os.environ["DB_HOST"],port=int(os.environ["DB_PORT"]),user=os.environ["DB_USER"],
  password=os.environ["DB_PASSWORD"],database=os.environ["DB_NAME"],charset="utf8mb4",autocommit=True)
[c.cursor().execute(s) for s in sql.split(";") if s.strip()]
PY
```

## ติดตั้ง
```bash
cd /opt/dunkin-pay-in-slip
python3 -m venv venv              # (มีอยู่แล้ว)
./venv/bin/pip install -r requirements.txt
cp .env.example .env              # แล้วใส่ DB_PASSWORD + SECRET_KEY
# OCR (จำเป็นสำหรับฟีเจอร์อ่านรูป — ต้อง sudo):
sudo apt install -y tesseract-ocr tesseract-ocr-tha
```
> ถ้ายังไม่ติดตั้ง tesseract ระบบยังใช้ได้ แต่ต้องกรอกตัวเลขเอง (OCR = ปิด)

## รัน dev
```bash
COOKIE_SECURE=0 ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8608 --reload
# เช็ก: curl http://localhost:8608/api/health   -> {"ocr_available": true/false}
```

## ติดตั้งเป็น service (ต้อง sudo)
```bash
sudo cp /opt/dunkin-pay-in-slip/dunkin-pay-in-slip.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dunkin-pay-in-slip
sudo systemctl status dunkin-pay-in-slip
```

## cloudflared (เพิ่มใน /etc/cloudflared/config.yml ก่อน catch-all)
```yaml
  - hostname: dunkin-pay-in-slip.mobile1234.site
    service: http://localhost:8608
```
แล้ว `sudo systemctl restart cloudflared`
> หมายเหตุ: ต้องเข้าผ่าน **https** (cloudflared) เพื่อให้กล้อง + GPS ของเบราว์เซอร์ทำงาน

## API
| Method | Path | ทำอะไร |
|---|---|---|
| POST | `/api/login` | `{username,password}` → เซ็ต cookie |
| POST | `/api/logout` | ออกจากระบบ |
| GET  | `/api/me` | ร้านปัจจุบัน + has_location + ocr_available |
| POST | `/api/ocr` | อัปรูป (preview) → ยอดที่ OCR อ่านได้ + ระยะห่าง (ไม่บันทึก) |
| POST | `/api/submit` | บันทึกใบนำฝาก (รูป + ยอด + พิกัด) |
| GET  | `/api/submissions` | ประวัติของร้าน |
