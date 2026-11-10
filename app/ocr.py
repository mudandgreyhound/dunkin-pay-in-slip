"""
OCR ด้วย Tesseract (local) + ตัว parse ฟิลด์เงิน
รองรับ 2 ชนิดเอกสาร:
  - day_end : Day End Report  -> ดึง "Cash Sales" = เงินสดที่ต้องนำฝาก
  - deposit : ใบนำฝาก (ธนาคาร/CP AXTRA/เซเว่น/ฯลฯ) -> ดึงยอดเงินที่ฝาก

ถ้าไม่มี tesseract ติดตั้ง: คืน available=False เพื่อให้ผู้ใช้กรอกเลขเอง
(ติดตั้ง: sudo apt install -y tesseract-ocr tesseract-ocr-tha)
"""
import re
import io
import shutil

try:
    import pytesseract
    from PIL import Image, ImageOps, ImageFilter
    _HAS_LIB = True
except Exception:
    _HAS_LIB = False

_HAS_BIN = shutil.which("tesseract") is not None


def available():
    return _HAS_LIB and _HAS_BIN


# ---------- รูปแบบตัวเลขเงิน เช่น 1,577.00 ----------
# ลำดับสำคัญ: จับแบบมีคอมมา (1,577.00) ก่อน แล้วค่อยแบบไม่มีคอมมา (1577.00 / 1577)
_MONEY = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)")
# เฉพาะที่มีทศนิยม .xx — รูปแบบ "เงิน" จริง (กัน Tax ID/เลขอ้างอิงที่ไม่มีจุด)
_MONEY_DEC = re.compile(r"\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2}")
# เพดานยอดเงินต่อใบ กัน OCR ไปจับเลข Tax ID/เบอร์โทร/เลขอ้างอิงที่ยาวผิดปกติ
MAX_AMOUNT = 10_000_000


def _to_float(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _valid(nums):
    return [n for n in nums if n is not None and 0 < n < MAX_AMOUNT]


def _line_amount(line):
    """ยอดเงินที่น่าจะถูกต้องในหนึ่งบรรทัด: เอาแบบมีทศนิยมก่อน แล้วค่อยแบบจำนวนเต็ม (มีเพดาน)"""
    dec = _valid(_to_float(x) for x in _MONEY_DEC.findall(line))
    if dec:
        return max(dec)
    alln = _valid(_to_float(x) for x in _MONEY.findall(line))
    return max(alln) if alln else None


def _best_money(text, decimals_only=False):
    """ยอดเงินที่ดีที่สุดทั้งเอกสาร: เน้นเลขมีทศนิยมและไม่เกินเพดาน (กันเลข ID)
    decimals_only=True: รับเฉพาะเลขมีทศนิยม (.xx) เท่านั้น ไม่งั้นคืน None
    (ใช้กับใบนำฝาก — ถ้าอ่านไม่ชัดให้ผู้ใช้กรอกเอง ดีกว่าเดามั่วจาก noise)"""
    dec = _valid(_to_float(x) for x in _MONEY_DEC.findall(text))
    if dec:
        return max(dec)
    if decimals_only:
        return None
    alln = _valid(_to_float(x) for x in _MONEY.findall(text))
    return max(alln) if alln else None


def _preprocess(img):
    """ปรับรูปให้ OCR อ่านง่ายขึ้น: หมุนตาม EXIF + เทาสเกล + คอนทราสต์ + คมชัด"""
    # สำคัญมาก: รูปจากมือถือมักมี EXIF orientation (ถ่ายแนวตั้ง/กลับหัว)
    # PIL ไม่หมุนให้อัตโนมัติ -> Tesseract อ่านรูปตะแคง/กลับหัวเป็นขยะ
    img = ImageOps.exif_transpose(img)
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    w, h = g.size
    longest = max(w, h)
    if longest < 1600:                 # เล็กไป -> ขยายให้ตัวอักษรใหญ่พอ
        s = 1600 / longest
        g = g.resize((int(w * s), int(h * s)))
    elif longest > 2600:               # ใหญ่มาก -> ย่อ ลด noise + เร็วขึ้น
        s = 2600 / longest
        g = g.resize((int(w * s), int(h * s)))
    g = g.filter(ImageFilter.SHARPEN)
    return g


def run_ocr(image_bytes, lang="tha+eng"):
    """คืนข้อความดิบจากรูป (หรือ '' ถ้าทำไม่ได้)"""
    if not available():
        return ""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = _preprocess(img)
        return pytesseract.image_to_string(img, lang=lang)
    except Exception:
        # ถ้าไม่มี language pack tha ให้ลอง eng อย่างเดียว
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img = _preprocess(img)
            return pytesseract.image_to_string(img, lang="eng")
        except Exception:
            return ""


def _amount_near(text, keywords):
    """
    หาเลขเงินในบรรทัดที่มี keyword ใด keyword หนึ่ง (ไม่สนตัวพิมพ์เล็กใหญ่)
    คืน (amount_float, line) ของค่าที่เจอแรก ๆ ที่สมเหตุผล
    """
    best = None
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in keywords):
            cand = _line_amount(line)
            if cand is not None and (best is None or cand > best[0]):
                best = (cand, line.strip())
    return best


def _amount_tiered(text, tiers):
    """ค้นหายอดตามลำดับความสำคัญของกลุ่มคำ: คืนผลจาก tier แรกที่เจอ
    (ใช้ให้ 'เงินฝาก' มาก่อน 'เงินสด' ในใบ 7-11/ธนาคารที่มีทั้งคู่คนละยอด)"""
    for kws in tiers:
        hit = _amount_near(text, kws)
        if hit:
            return hit
    return None


def _find_date(text):
    """หา DATE แบบ dd/mm/yyyy หรือ dd/mm/yy -> คืน 'YYYY-MM-DD' (พยายามแปลง พ.ศ.->ค.ศ.)"""
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", text)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:          # 69 -> 2569(พ.ศ.) ; เดาเป็น 2000+yy ของ ค.ศ. ไม่ได้ จึงลองทั้งคู่
        y = 2500 + y if y >= 50 else 2600 + y  # พ.ศ. สองหลัก
    if y > 2400:         # เป็น พ.ศ. -> แปลงเป็น ค.ศ.
        y -= 543
    try:
        if 1 <= mo <= 12 and 1 <= d <= 31 and 2000 <= y <= 2100:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        pass
    return None


def parse_day_end(text):
    """ดึง Cash Sales (เงินสดที่ต้องนำฝาก) จาก Day End Report"""
    hit = _amount_near(text, ["cash sales", "cash  sales"])
    # เผื่อ OCR เพี้ยน: ลอง 'cash' เดี่ยว ๆ หรือ 'ขาย' เงินสด
    if not hit:
        hit = _amount_near(text, ["cash", "เงินสด"])
    cash = hit[0] if hit else None
    return {
        "doc_type": "day_end",
        "cash_sales": cash,
        "biz_date": _find_date(text),
        "matched_line": hit[1] if hit else None,
    }


def parse_deposit(text):
    """ดึงยอดเงินฝากจากใบนำฝาก (ธนาคาร/CP AXTRA/เซเว่น/ฯลฯ)"""
    # ลำดับความสำคัญ: "เงินฝาก/ฝาก" ต้องมาก่อน "เงินสด"
    # (ใบ 7-11/ธนาคารมี เงินฝาก + เงินสด(ที่ลูกค้าจ่าย) + เงินทอน คนละยอด — ต้องการ "เงินฝาก")
    hit = _amount_tiered(text, [
        ["ฝาก", "deposit"],                                    # เงินฝาก/ยอดฝาก/นำฝาก ← ยอดที่ต้องการ
        ["ยอดรวม", "รวมเงิน", "จำนวนเงิน", "total", "amount"],  # ยอดรวม
        ["เงินสด", "cash"],                                    # เงินสดลูกค้าจ่าย (เผื่อไม่มีบรรทัดฝาก)
    ])
    amount = hit[0] if hit else _best_money(text, decimals_only=True)
    # เดาช่องทาง
    low = text.lower()
    channel = None
    # เรียงจากเฉพาะเจาะจงไปทั่วไป; เผื่อ OCR อ่านเพี้ยน (axtra->aatra, cp a...)
    for key, name in [("axtra", "cpaxtra"), ("aatra", "cpaxtra"), ("cp a", "cpaxtra"),
                      ("7-eleven", "seven"), ("7-11", "seven"), ("เซเว่น", "seven"),
                      ("counter service", "counter"), ("counter", "counter"),
                      ("ธนาคาร", "bank"), ("bank", "bank"), ("bbl", "bank"),
                      ("kbank", "bank"), ("scb", "bank"), ("ktb", "bank")]:
        if key in low:
            channel = name
            break
    return {
        "doc_type": "deposit",
        "deposit_amount": amount,
        "channel": channel,
        "matched_line": hit[1] if hit else None,
    }


def analyze(image_bytes, kind):
    """
    kind = 'day_end' | 'deposit'
    คืน dict: available, text, + ฟิลด์ที่ parse ได้
    """
    text = run_ocr(image_bytes)
    out = {"available": available(), "text": text}
    if kind == "day_end":
        out.update(parse_day_end(text))
    else:
        out.update(parse_deposit(text))
    return out
