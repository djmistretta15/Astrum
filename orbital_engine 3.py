"""
Astrum Orbital Engine
SGP4 propagation + conjunction risk graph + cascade analysis
Data: Celestrak live TLE catalogs with disk cache + seed fallback
"""

import math
from datetime import datetime, timezone, timedelta
from sgp4.api import Satrec, jday
import numpy as np
import networkx as nx
from dataclasses import dataclass
from typing import List, Optional, Dict

from tle_loader import TLELoader
from tle_seed import SEED_TLES as _SEED_TLES


CATEGORY_MAP = {
    "STARLINK": "Starlink", "ONEWEB": "OneWeb", "ISS": "Station",
    "ZARYA": "Station", "DEB": "Debris", "GPS": "Navigation",
    "NOAA": "Weather", "IRIDIUM": "Comms", "PLANET": "EO",
    "GLONASS": "Navigation", "GALILEO": "Navigation",
}

def categorize(name: str) -> str:
    n = name.upper()
    for k, v in CATEGORY_MAP.items():
        if k in n:
            return v
    return "Other"

def risk_label(miss_km: float) -> str:
    if miss_km < 0.5:  return "CRITICAL"
    if miss_km < 2.0:  return "HIGH"
    if miss_km < 10.0: return "MEDIUM"
    return "LOW"

def estimate_pc(miss_km: float) -> float:
    hard_body = 0.010
    sigma = max(miss_km / 3.0, 0.05)
    return min(math.exp(-0.5 * (miss_km / sigma) ** 2) * (hard_body / sigma) ** 2, 1.0)

def estimate_delta_v(miss_km: float, tca_hours: float) -> float:
    target = 5.0
    if miss_km >= target: return 0.0
    deficit = target - miss_km
    return round((deficit * 1000) / (max(tca_hours, 0.5) * 3600), 3)


@dataclass
class Satellite:
    name: str
    norad_id: str
    category: str
    satrec: object
    position: Optional[np.ndarray] = None
    velocity: Optional[np.ndarray] = None


@dataclass
class ConjunctionEvent:
    sat_a: str
    sat_b: str
    tca: datetime
    miss_distance: float
    closing_speed: float
    probability: float
    risk_level: str
    delta_v_cost: float
    cascade_risk: float


class OrbitalEngine:
    def __init__(self, cache_dir: str = "./tle_cache", live: bool = True):
        self.satellites: Dict[str, Satellite] = {}
        self.conjunctions: List[ConjunctionEvent] = []
        self.risk_graph = nx.Graph()
        self.last_updated: Optional[datetime] = None
        self.data_source: str = "seed"
        self._loader = TLELoader(cache_dir=cache_dir)
        self._load_tles(live=live)

    def _load_tles(self, live: bool = True):
        """Load TLEs from Celestrak (live) or seed data (fallback)."""
        if live:
            tles = self._loader.load()
            if tles and tles is not _SEED_TLES:
                self.data_source = "live"
            else:
                self.data_source = "seed"
        else:
            tles = _SEED_TLES
            self.data_source = "seed"

        loaded = 0
        for name, line1, line2 in tles:
            try:
                satrec = Satrec.twoline2rv(line1, line2)
                self.satellites[name] = Satellite(
                    name=name,
                    norad_id=line1[2:7].strip(),
                    category=categorize(name),
                    satrec=satrec
                )
                loaded += 1
            except Exception as e:
                pass  # bad TLE line — skip silently

        print(f"[Astrum] Loaded {loaded} satellites ({self.data_source})")

    def reload_tles(self, force: bool = False):
        """Hot-reload TLE data. Call periodically or on-demand."""
        self.satellites.clear()
        tles = self._loader.load(force=force)
        loaded = 0
        for name, line1, line2 in tles:
            try:
                satrec = Satrec.twoline2rv(line1, line2)
                self.satellites[name] = Satellite(
                    name=name,
                    norad_id=line1[2:7].strip(),
                    category=categorize(name),
                    satrec=satrec
                )
                loaded += 1
            except Exception:
                pass
        self.data_source = "live" if tles is not _SEED_TLES else "seed"
        print(f"[Astrum] Reloaded {loaded} satellites ({self.data_source})")
        return loaded

    def propagate_all(self, t: datetime = None) -> Dict[str, dict]:
        if t is None:
            t = datetime.now(timezone.utc)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                      t.second + t.microsecond / 1e6)
        states = {}
        for name, sat in self.satellites.items():
            e, r, v = sat.satrec.sgp4(jd, fr)
            if e == 0:
                sat.position = np.array(r)
                sat.velocity = np.array(v)
                states[name] = {
                    "position": list(r),
                    "velocity": list(v),
                    "altitude": round(np.linalg.norm(r) - 6371.0, 2),
                    "speed": round(np.linalg.norm(v), 4),
                    "category": sat.category,
                }
        return states

    def compute_conjunctions(self, lookahead_hours: float = 24.0) -> List[ConjunctionEvent]:
        conjunctions = []
        t_now = datetime.now(timezone.utc)
        steps = int(lookahead_hours * 12)  # 5-min steps
        dt_step = timedelta(minutes=5)
        sat_names = list(self.satellites.keys())

        # Build trajectory tables
        trajectories: Dict[str, list] = {n: [] for n in sat_names}
        times = []
        for i in range(steps):
            t = t_now + i * dt_step
            times.append(t)
            states = self.propagate_all(t)
            for name in sat_names:
                pos = np.array(states[name]["position"]) if name in states else None
                trajectories[name].append(pos)

        # Pairwise TCA search
        for i in range(len(sat_names)):
            for j in range(i + 1, len(sat_names)):
                a, b = sat_names[i], sat_names[j]
                min_dist = float('inf')
                min_idx = 0
                closing = 0.0
                for k in range(len(times)):
                    pa, pb = trajectories[a][k], trajectories[b][k]
                    if pa is None or pb is None:
                        continue
                    d = np.linalg.norm(pa - pb)
                    if d < min_dist:
                        min_dist = d
                        min_idx = k
                        if k > 0:
                            pa0 = trajectories[a][k-1]
                            pb0 = trajectories[b][k-1]
                            if pa0 is not None and pb0 is not None:
                                closing = (np.linalg.norm(pa0 - pb0) - d) / 300.0

                if min_dist < 200.0:
                    tca = times[min_idx]
                    tca_hrs = (tca - t_now).total_seconds() / 3600
                    pc = estimate_pc(min_dist)
                    conjunctions.append(ConjunctionEvent(
                        sat_a=a, sat_b=b, tca=tca,
                        miss_distance=round(min_dist, 3),
                        closing_speed=round(abs(closing), 4),
                        probability=round(pc, 8),
                        risk_level=risk_label(min_dist),
                        delta_v_cost=estimate_delta_v(min_dist, tca_hrs),
                        cascade_risk=round(min(pc * 50, 0.95), 4)
                    ))

        rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        conjunctions.sort(key=lambda c: (rank[c.risk_level], c.miss_distance))
        self.conjunctions = conjunctions
        self.last_updated = t_now
        self._build_risk_graph()
        return conjunctions

    def _build_risk_graph(self):
        G = nx.Graph()
        weights = {"CRITICAL": 1.0, "HIGH": 0.7, "MEDIUM": 0.4, "LOW": 0.1}
        for name, sat in self.satellites.items():
            alt = float(np.linalg.norm(sat.position) - 6371.0) if sat.position is not None else 0.0
            G.add_node(name, category=sat.category, altitude=alt)
        for c in self.conjunctions:
            G.add_edge(c.sat_a, c.sat_b,
                       miss_distance=c.miss_distance,
                       risk_level=c.risk_level,
                       weight=weights[c.risk_level],
                       probability=c.probability,
                       delta_v=c.delta_v_cost)
        self.risk_graph = G

    def cascade_analysis(self, sat_name: str) -> dict:
        if sat_name not in self.risk_graph:
            return {"primary_affected": [], "secondary_affected": [], "cascade_score": 0, "kessler_risk": False}
        neighbors = list(self.risk_graph.neighbors(sat_name))
        cascade_score = sum(self.risk_graph[sat_name][n]["weight"] for n in neighbors)
        second = {nn for n in neighbors for nn in self.risk_graph.neighbors(n) if nn != sat_name}
        return {
            "primary_affected": neighbors,
            "secondary_affected": list(second),
            "cascade_score": round(cascade_score, 3),
            "kessler_risk": cascade_score > 2.0
        }

    def get_summary(self) -> dict:
        by_risk = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        total_dv = 0.0
        risk_scores: Dict[str, float] = {}
        w = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        for c in self.conjunctions:
            by_risk[c.risk_level] += 1
            total_dv += c.delta_v_cost
            for s in [c.sat_a, c.sat_b]:
                risk_scores[s] = risk_scores.get(s, 0) + w[c.risk_level]
        top = sorted(risk_scores.items(), key=lambda x: -x[1])[:5]
        return {
            "total_conjunctions": len(self.conjunctions),
            "by_risk": by_risk,
            "total_delta_v_budget": round(total_dv, 2),
            "satellites_tracked": len(self.satellites),
            "data_source": self.data_source,
            "top_risk_satellites": [{"name": k, "score": v} for k, v in top],
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "graph_edges": self.risk_graph.number_of_edges(),
            "graph_nodes": self.risk_graph.number_of_nodes(),
            "tle_cache_status": self._loader.get_status(),
        }
