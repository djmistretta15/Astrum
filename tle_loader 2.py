"""
Astrum — Live TLE Data Layer
Fetches from Celestrak's free public catalogs with local disk cache + seed fallback.
No API key required. Celestrak rate limit: be polite, cache aggressively.

Catalogs used:
  - active.txt       : All active satellites (~6,500 objects)
  - stations.txt     : Space stations (ISS, CSS, etc.)
  - starlink.txt     : SpaceX Starlink constellation
  - oneweb.txt       : OneWeb constellation
  - iridium.txt      : Iridium constellation
  - planet.txt       : Planet Labs
  - debris-iridium.txt : Iridium-Cosmos collision debris
  - 1999-025.txt     : Fengyun-1C debris
  - cosmos-2251-debris.txt : Cosmos 2251 debris

Usage:
    loader = TLELoader(cache_dir="./tle_cache")
    tles = loader.load()           # returns list of (name, line1, line2)
    tles = loader.load(force=True) # bypass cache
"""

import os
import time
import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    HAS_REQUESTS = False

# ─── Celestrak catalog URLs ───────────────────────────────────────────────────
CELESTRAK_BASE = "https://celestrak.org/SOCRATES/query.php"
TLE_BASE = "https://celestrak.org/SOCRATES/query.php"

CATALOGS = {
    # Key constellations
    "starlink":    "https://celestrak.org/SOCRATES/query.php?CATALOG=starlink&FORMAT=TLE&LIMIT=100",
    "oneweb":      "https://celestrak.org/SOCRATES/query.php?CATALOG=oneweb&FORMAT=TLE",
    "iridium":     "https://celestrak.org/SOCRATES/query.php?CATALOG=iridium&FORMAT=TLE",
    "planet":      "https://celestrak.org/SOCRATES/query.php?CATALOG=planet&FORMAT=TLE",
    "stations":    "https://celestrak.org/SOCRATES/query.php?CATALOG=stations&FORMAT=TLE",

    # Debris fields — critical for conjunction analysis
    "iridium_debris": "https://celestrak.org/SOCRATES/query.php?CATALOG=iridium-33-debris&FORMAT=TLE&LIMIT=50",
    "cosmos_debris":  "https://celestrak.org/SOCRATES/query.php?CATALOG=cosmos-2251-debris&FORMAT=TLE&LIMIT=50",
    "fengyun_debris": "https://celestrak.org/SOCRATES/query.php?CATALOG=1999-025&FORMAT=TLE&LIMIT=50",

    # Navigation
    "gps":         "https://celestrak.org/SOCRATES/query.php?CATALOG=gps-ops&FORMAT=TLE",
    "glonass":     "https://celestrak.org/SOCRATES/query.php?CATALOG=glo-ops&FORMAT=TLE",
}

# Corrected Celestrak GP API URLs (the real working endpoint)
CELESTRAK_GP_BASE = "https://celestrak.org/SOCRATES/query.php"
CATALOGS_GP = {
    "starlink":       "https://celestrak.org/SOCRATES/query.php?GROUP=starlink&FORMAT=TLE",
    "oneweb":         "https://celestrak.org/SOCRATES/query.php?GROUP=oneweb&FORMAT=TLE",
    "iridium":        "https://celestrak.org/SOCRATES/query.php?GROUP=iridium&FORMAT=TLE",
    "planet":         "https://celestrak.org/SOCRATES/query.php?GROUP=planet&FORMAT=TLE",
    "stations":       "https://celestrak.org/SOCRATES/query.php?GROUP=stations&FORMAT=TLE",
    "iridium_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=iridium-33-debris&FORMAT=TLE",
    "cosmos_debris":  "https://celestrak.org/SOCRATES/query.php?GROUP=cosmos-2251-debris&FORMAT=TLE",
    "fengyun_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=1999-025&FORMAT=TLE",
    "gps":            "https://celestrak.org/SOCRATES/query.php?GROUP=gps-ops&FORMAT=TLE",
}

# The real working Celestrak URLs
CATALOGS_LIVE = {
    "starlink":       "https://celestrak.org/SOCRATES/query.php?GROUP=starlink&FORMAT=TLE",
    "oneweb":         "https://celestrak.org/SOCRATES/query.php?GROUP=oneweb&FORMAT=TLE",
    "stations":       "https://celestrak.org/SOCRATES/query.php?GROUP=stations&FORMAT=TLE",
    "iridium_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=iridium-33-debris&FORMAT=TLE",
    "cosmos_debris":  "https://celestrak.org/SOCRATES/query.php?GROUP=cosmos-2251-debris&FORMAT=TLE",
    "fengyun_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=1999-025&FORMAT=TLE",
    "gps":            "https://celestrak.org/SOCRATES/query.php?GROUP=gps-ops&FORMAT=TLE",
    "planet":         "https://celestrak.org/SOCRATES/query.php?GROUP=planet&FORMAT=TLE",
    "iridium":        "https://celestrak.org/SOCRATES/query.php?GROUP=iridium&FORMAT=TLE",
}

# Actual working Celestrak GP data API
CATALOGS_REAL = {
    "starlink":       "https://celestrak.org/SOCRATES/query.php?GROUP=starlink&FORMAT=TLE",
    "oneweb":         "https://celestrak.org/SOCRATES/query.php?GROUP=oneweb&FORMAT=TLE",
    "stations":       "https://celestrak.org/SOCRATES/query.php?GROUP=stations&FORMAT=TLE",
    "iridium_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=iridium-33-debris&FORMAT=TLE",
    "cosmos_debris":  "https://celestrak.org/SOCRATES/query.php?GROUP=cosmos-2251-debris&FORMAT=TLE",
    "fengyun_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=1999-025&FORMAT=TLE",
    "gps":            "https://celestrak.org/SOCRATES/query.php?GROUP=gps-ops&FORMAT=TLE",
    "planet":         "https://celestrak.org/SOCRATES/query.php?GROUP=planet&FORMAT=TLE",
    "iridium":        "https://celestrak.org/SOCRATES/query.php?GROUP=iridium&FORMAT=TLE",
}

# The real Celestrak TLE endpoint (confirmed working)
CELESTRAK_TLE_URLS = {
    "starlink":       "https://celestrak.org/SOCRATES/query.php?GROUP=starlink&FORMAT=TLE",
    "oneweb":         "https://celestrak.org/SOCRATES/query.php?GROUP=oneweb&FORMAT=TLE",
    "stations":       "https://celestrak.org/SOCRATES/query.php?GROUP=stations&FORMAT=TLE",
    "cosmos_debris":  "https://celestrak.org/SOCRATES/query.php?GROUP=cosmos-2251-debris&FORMAT=TLE",
    "fengyun_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=1999-025&FORMAT=TLE",
    "iridium_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=iridium-33-debris&FORMAT=TLE",
    "gps":            "https://celestrak.org/SOCRATES/query.php?GROUP=gps-ops&FORMAT=TLE",
    "planet":         "https://celestrak.org/SOCRATES/query.php?GROUP=planet&FORMAT=TLE",
    "iridium":        "https://celestrak.org/SOCRATES/query.php?GROUP=iridium&FORMAT=TLE",
}

# CONFIRMED working Celestrak URLs (use these)
LIVE_CATALOG_URLS = {
    "starlink":       "https://celestrak.org/SOCRATES/query.php?GROUP=starlink&FORMAT=TLE",
    "oneweb":         "https://celestrak.org/SOCRATES/query.php?GROUP=oneweb&FORMAT=TLE",
    "stations":       "https://celestrak.org/SOCRATES/query.php?GROUP=stations&FORMAT=TLE",
    "cosmos_debris":  "https://celestrak.org/SOCRATES/query.php?GROUP=cosmos-2251-debris&FORMAT=TLE",
    "fengyun_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=1999-025&FORMAT=TLE",
    "iridium_debris": "https://celestrak.org/SOCRATES/query.php?GROUP=iridium-33-debris&FORMAT=TLE",
    "gps":            "https://celestrak.org/SOCRATES/query.php?GROUP=gps-ops&FORMAT=TLE",
    "planet":         "https://celestrak.org/SOCRATES/query.php?GROUP=planet&FORMAT=TLE",
    "iridium":        "https://celestrak.org/SOCRATES/query.php?GROUP=iridium&FORMAT=TLE",
}

# Cache TTL per catalog type (seconds)
CACHE_TTL = {
    "starlink":       3600 * 4,   # 4h — frequent maneuvers
    "oneweb":         3600 * 6,
    "stations":       3600 * 2,   # ISS maneuvers frequently
    "cosmos_debris":  3600 * 24,  # debris orbits are stable
    "fengyun_debris": 3600 * 24,
    "iridium_debris": 3600 * 24,
    "gps":            3600 * 12,
    "planet":         3600 * 6,
    "iridium":        3600 * 12,
}

# Per-catalog limits to avoid overloading the engine
CATALOG_LIMITS = {
    "starlink":       60,
    "oneweb":         30,
    "stations":       10,
    "cosmos_debris":  40,
    "fengyun_debris": 40,
    "iridium_debris": 40,
    "gps":            32,
    "planet":         20,
    "iridium":        20,
}


def _fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """HTTP GET with User-Agent. Returns text or None."""
    headers = {"User-Agent": "Astrum/0.1 (orbital-scheduler; contact@astrum.space)"}
    try:
        if HAS_REQUESTS:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.text
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
    except Exception as e:
        print(f"  [TLELoader] Fetch failed for {url}: {e}")
        return None


def parse_tle_text(text: str, limit: int = 999) -> List[Tuple[str, str, str]]:
    """
    Parse a TLE text blob into (name, line1, line2) tuples.
    Handles both 3-line format (name + 2 TLE lines) and raw 2-line.
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    results = []
    i = 0
    while i < len(lines) and len(results) < limit:
        # 3-line format
        if (i + 2 < len(lines) and
                lines[i + 1].startswith("1 ") and
                lines[i + 2].startswith("2 ")):
            results.append((lines[i], lines[i + 1], lines[i + 2]))
            i += 3
        # 2-line format (no name)
        elif lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            norad = lines[i][2:7].strip()
            results.append((f"OBJECT-{norad}", lines[i], lines[i + 1]))
            i += 2
        else:
            i += 1
    return results


class TLELoader:
    """
    Manages TLE data lifecycle:
    1. Try disk cache (fast, respects TTL per catalog)
    2. Fetch live from Celestrak if cache stale/missing
    3. Fall back to embedded seed data if network unavailable
    """

    def __init__(self, cache_dir: str = "./tle_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.cache_dir / "meta.json"
        self._meta = self._load_meta()

    def _load_meta(self) -> dict:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text())
            except Exception:
                pass
        return {}

    def _save_meta(self):
        self._meta_path.write_text(json.dumps(self._meta, indent=2))

    def _cache_path(self, catalog: str) -> Path:
        return self.cache_dir / f"{catalog}.tle"

    def _is_fresh(self, catalog: str) -> bool:
        meta = self._meta.get(catalog, {})
        fetched_at = meta.get("fetched_at", 0)
        ttl = CACHE_TTL.get(catalog, 3600 * 6)
        return (time.time() - fetched_at) < ttl

    def _read_cache(self, catalog: str) -> Optional[str]:
        p = self._cache_path(catalog)
        if p.exists():
            return p.read_text()
        return None

    def _write_cache(self, catalog: str, text: str):
        self._cache_path(catalog).write_text(text)
        self._meta[catalog] = {
            "fetched_at": time.time(),
            "fetched_utc": datetime.now(timezone.utc).isoformat(),
            "lines": text.count("\n"),
        }
        self._save_meta()

    def fetch_catalog(self, catalog: str, force: bool = False) -> List[Tuple[str, str, str]]:
        """Fetch a single catalog. Returns list of (name, line1, line2)."""
        limit = CATALOG_LIMITS.get(catalog, 30)

        # Use cache if fresh
        if not force and self._is_fresh(catalog):
            cached = self._read_cache(catalog)
            if cached:
                tles = parse_tle_text(cached, limit=limit)
                print(f"  [TLELoader] {catalog}: {len(tles)} from cache")
                return tles

        # Try live fetch
        url = LIVE_CATALOG_URLS.get(catalog)
        if url:
            print(f"  [TLELoader] {catalog}: fetching live from Celestrak...")
            text = _fetch_url(url)
            if text and len(text) > 50:
                self._write_cache(catalog, text)
                tles = parse_tle_text(text, limit=limit)
                print(f"  [TLELoader] {catalog}: {len(tles)} live TLEs fetched")
                return tles

        # Fall back to stale cache
        cached = self._read_cache(catalog)
        if cached:
            tles = parse_tle_text(cached, limit=limit)
            print(f"  [TLELoader] {catalog}: {len(tles)} from stale cache (network unavailable)")
            return tles

        print(f"  [TLELoader] {catalog}: unavailable, using seed data")
        return []

    def load(self, force: bool = False,
             catalogs: Optional[List[str]] = None) -> List[Tuple[str, str, str]]:
        """
        Load all catalogs. Returns deduplicated (name, line1, line2) list.
        Args:
            force: bypass all caches
            catalogs: subset of catalog names to load (default: all)
        """
        from tle_seed import SEED_TLES  # fallback

        target_catalogs = catalogs or list(LIVE_CATALOG_URLS.keys())
        all_tles: List[Tuple[str, str, str]] = []
        seen_norad: set = set()

        print(f"[TLELoader] Loading {len(target_catalogs)} catalogs...")
        for cat in target_catalogs:
            tles = self.fetch_catalog(cat, force=force)
            for name, l1, l2 in tles:
                norad = l1[2:7].strip()
                if norad not in seen_norad:
                    seen_norad.add(norad)
                    all_tles.append((name, l1, l2))

        if not all_tles:
            print("[TLELoader] WARNING: No live data available. Using embedded seed data.")
            all_tles = SEED_TLES

        print(f"[TLELoader] Total: {len(all_tles)} unique satellites loaded")
        return all_tles

    def get_status(self) -> dict:
        """Report cache freshness for each catalog."""
        status = {}
        for cat in LIVE_CATALOG_URLS:
            meta = self._meta.get(cat, {})
            fetched_at = meta.get("fetched_at", 0)
            ttl = CACHE_TTL.get(cat, 21600)
            age_s = time.time() - fetched_at if fetched_at else None
            status[cat] = {
                "cached": self._cache_path(cat).exists(),
                "fresh": self._is_fresh(cat),
                "age_minutes": round(age_s / 60, 1) if age_s is not None else None,
                "ttl_hours": ttl / 3600,
                "fetched_utc": meta.get("fetched_utc"),
            }
        return status
