"""
ล็อกอินแบบง่ายตามสเปก:
  username = GD + รหัสร้าน   (เช่น GD0143)
  password = รหัสร้าน        (เช่น 0143)
ตรวจว่ารหัสร้านมีจริงใน dim_shop; ออก token เซ็นชื่อ (itsdangerous) เก็บใน cookie
"""
import os
import re
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
