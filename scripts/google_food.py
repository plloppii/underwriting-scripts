"""Optional Google Places food facts; independent of Notion."""
import os
import math
from property_sources import SESSION, haversine_mi

def is_food_primary(kind: str | None) -> bool:
    """A food business's primary type; secondary cafes inside retail stores do not qualify."""
    return bool(kind and (kind in {"restaurant", "cafe", "bakery", "coffee_shop", "tea_house"} or kind.endswith("_restaurant")))


def places_food(lat: float, lon: float, radius_m: int = 800, limit: int = 5) -> list[dict] | None:
    """Cafes, restaurants and bakeries with ratings from Google Places (New). None when no key is set."""
    key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    r = SESSION.post("https://places.googleapis.com/v1/places:searchNearby", timeout=30,
                     headers={"X-Goog-Api-Key": key, "Content-Type": "application/json",
                              "X-Goog-FieldMask": "places.displayName,places.location,places.rating,places.userRatingCount,places.primaryType"},
                     json={"includedTypes": ["restaurant", "cafe", "bakery"], "maxResultCount": 20, "rankPreference": "POPULARITY",
                           "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": radius_m}}})
    if r.status_code >= 400:
        raise RuntimeError(f"places -> {r.status_code}: {r.text[:200]}")
    out = []
    for pl in r.json().get("places", []):
        if not pl.get("rating") or not is_food_primary(pl.get("primaryType")):
            continue
        out.append({"name": pl["displayName"]["text"], "kind": (pl.get("primaryType") or "restaurant").replace("_", " "),
                    "rating": pl["rating"], "ratings": int(pl.get("userRatingCount") or 0),
                    "lat": pl["location"]["latitude"], "lon": pl["location"]["longitude"]})
    return sorted(out, key=lambda f: -(f["rating"] * math.log10(f["ratings"] + 1)))[:limit]
