"""คำนวณระยะทางระหว่างพิกัด (Haversine)"""
import math


def haversine_m(lat1, lng1, lat2, lng2):
    """คืนระยะทางเป็นเมตรระหว่างสองพิกัด (lat/lng หน่วยองศา)"""
    R = 6371000.0  # รัศมีโลก (เมตร)
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_info(user_lat, user_lng, shop_loc):
    """
    shop_loc = dict จาก shop_location (lat, lng, radius_m) หรือ None
    คืน dict: distance_m, within_radius, radius_m, km (ห่างกี่กิโล)
    """
    if not shop_loc or shop_loc.get("lat") is None or user_lat is None:
        return {"distance_m": None, "within_radius": None, "radius_m": None, "km": None}
    dist = haversine_m(user_lat, user_lng, shop_loc["lat"], shop_loc["lng"])
    radius = int(shop_loc.get("radius_m") or 120)
    return {
        "distance_m": round(dist),
        "within_radius": 1 if dist <= radius else 0,
        "radius_m": radius,
        "km": round(dist / 1000.0, 3),
    }
