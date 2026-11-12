"""
ล็อกอินแบบง่ายตามสเปก:
  username = GD + รหัสร้าน   (เช่น GD0143)
  password = รหัสร้าน        (เช่น 0143)
ตรวจว่ารหัสร้านมีจริงใน dim_shop; ออก token เซ็นชื่อ (itsdangerous) เก็บใน cookie
"""
import os
import re
import hmac
import hashlib
import secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from . import db

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
COOKIE_NAME = "payin_session"
MAX_AGE = 60 * 60 * 12  # 12 ชั่วโมง

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="payin-login")

_USER_RE = re.compile(r"^GD(\d{3,6})$", re.IGNORECASE)


def authenticate(username, password):
    """
    คืน dict ข้อมูลร้านถ้าสำเร็จ, หรือ None ถ้าไม่ผ่าน
    กติกา: username = GD<shop_code>, password = <shop_code>, และ shop_code ต้องมีใน dim_shop
    """
    if not username or not password:
        return None
    m = _USER_RE.match(username.strip())
    if not m:
        return None
    shop_code = m.group(1)
    if password.strip() != shop_code:
        return None
    shop = db.get_shop(shop_code)
    if not shop:
        return None
    return shop


def make_token(shop_code):
    return _serializer.dumps({"shop_code": shop_code})


def read_token(token):
    """คืน shop_code ถ้า token ถูกต้องและไม่หมดอายุ, ไม่งั้น None"""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
        return data.get("shop_code")
    except (BadSignature, SignatureExpired):
        return None


# ---------- ผู้ตรวจสอบ (admin) ----------
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_COOKIE = "payin_admin"
_admin_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="payin-admin")


def authenticate_admin(username, password):
    """คืน username ถ้าถูกต้อง, ไม่งั้น None"""
    if not ADMIN_PASSWORD:
        return None
    if (username or "").strip() == ADMIN_USER and (password or "") == ADMIN_PASSWORD:
        return ADMIN_USER
    return None


def make_admin_token(username):
    return _admin_serializer.dumps({"u": username})


def read_admin_token(token):
    if not token:
        return None
    try:
        data = _admin_serializer.loads(token, max_age=MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


# ---------- รหัสผ่าน (pbkdf2_sha256, stdlib — ไม่ต้องลง bcrypt/passlib) ----------
_PBKDF2_ITERS = 200_000


def hash_password(password):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                             bytes.fromhex(salt), _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt}${dk.hex()}"


def verify_password(password, stored):
    if not stored:
        return False
    try:
        algo, iters, salt, h = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                 bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), h)
    except Exception:
        return False


# ---------- DM (ผู้จัดการเขต — เห็นเฉพาะสาขาที่ดูแล) ----------
DM_COOKIE = "payin_dm"
_dm_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="payin-dm")


def authenticate_dm(username, password):
    """คืน dict ข้อมูล DM ถ้าถูกต้องและ active, ไม่งั้น None"""
    u = (username or "").strip()
    if not u or not password:
        return None
    user = db.get_dm_user(u)
    if not user or not user.get("active"):
        return None
    if not verify_password(password, user.get("password_hash")):
        return None
    return user


def make_dm_token(username):
    return _dm_serializer.dumps({"u": username})


def read_dm_token(token):
    if not token:
        return None
    try:
        data = _dm_serializer.loads(token, max_age=MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None
