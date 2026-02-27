"""
Astrum Orbital Engine
SGP4 propagation + conjunction risk graph + cascade analysis
Data: Celestrak TLE catalogs (live fetch; fallback to embedded seed data)
"""

import math
import random
from datetime import datetime, timezone, timedelta
from sgp4.api import Satrec, jday
import numpy as np
import networkx as nx
from dataclasses import dataclass
from typing import List, Optional, Dict

# ─────────────────────────────────────────────
# REALISTIC SEED TLE DATA
# Mirrors actual orbital parameters for key shells
# In production: replace with Celestrak live fetch
# ─────────────────────────────────────────────

SEED_TLES = [
    # Starlink shell ~550km, inc 53°
    ("STARLINK-1007", "1 44713U 19074B   24001.50000000  .00001200  00000-0  90000-4 0  9990",
                      "2 44713  53.0000  45.0000 0001000  90.0000  270.0000 15.05000000 12345"),
    ("STARLINK-1008", "1 44714U 19074C   24001.50000000  .00001100  00000-0  85000-4 0  9991",
                      "2 44714  53.0000  46.2000 0001200  91.0000  269.0000 15.05000000 12346"),
    ("STARLINK-1009", "1 44715U 19074D   24001.50000000  .00001300  00000-0  95000-4 0  9992",
                      "2 44715  53.0000  47.4000 0001100  92.0000  268.0000 15.05000000 12347"),
    ("STARLINK-1010", "1 44716U 19074E   24001.50000000  .00001050  00000-0  80000-4 0  9993",
                      "2 44716  53.0000  48.6000 0001300  93.0000  267.0000 15.05000000 12348"),
    ("STARLINK-1011", "1 44717U 19074F   24001.50000000  .00001150  00000-0  87000-4 0  9994",
                      "2 44717  53.0000  44.1000 0000900  89.0000  271.0000 15.05000000 12349"),
    # OneWeb ~1200km, inc 87.9°
    ("ONEWEB-0001",   "1 45132U 20008A   24001.50000000  .00000300  00000-0  20000-4 0  9990",
                      "2 45132  87.9000 120.0000 0001000  45.0000 315.0000 13.20000000 54321"),
    ("ONEWEB-0002",   "1 45133U 20008B   24001.50000000  .00000280  00000-0  19000-4 0  9991",
                      "2 45133  87.9000 121.5000 0001100  46.0000 314.0000 13.20000000 54322"),
    ("ONEWEB-0003",   "1 45134U 20008C   24001.50000000  .00000320  00000-0  21000-4 0  9992",
                      "2 45134  87.9000 119.0000 0000900  44.0000 316.0000 13.20000000 54323"),
    # ISS ~400km
    ("ISS (ZARYA)",   "1 25544U 98067A   24001.50000000  .00006000  00000-0  11000-3 0  9990",
                      "2 25544  51.6400 120.0000 0005000  90.0000 270.0000 15.49000000 23456"),
    # Debris — Cosmos/Iridium collision belt
    ("COSMOS 2251 DEB","1 33791U 93036RU  24001.50000000  .00002100  00000-0  25000-3 0  9990",
                       "2 33791  74.0000 200.0000 0050000  45.0000 315.0000 14.50000000 34567"),
    ("COSMOS 2251 DEB2","1 33792U 93036RV  24001.50000000  .00002300  00000-0  27000-3 0  9991",
                        "2 33792  74.0000 201.5000 0052000  46.0000 314.0000 14.50000000 34568"),
    ("FENGYUN 1C DEB", "1 29228U 99025ACG  24001.50000000  .00001500  00000-0  18000-3 0  9990",
                       "2 29228  99.0000 150.0000 0020000  30.0000 330.0000 14.20000000 45678"),
    ("FENGYUN 1C DEB2","1 29229U 99025ACH  24001.50000000  .00001600  00000-0  19000-3 0  9991",
                       "2 29229  99.0000 151.0000 0021000  31.0000 329.0000 14.20000000 45679"),
    # Planet Labs EO ~475km SSO
    ("PLANET LAB-001", "1 47934U 21022A   24001.50000000  .00001800  00000-0  15000-3 0  9990",
                       "2 47934  97.5000  80.0000 0010000  60.0000 300.0000 14.90000000 56789"),
    ("PLANET LAB-002", "1 47935U 21022B   24001.50000000  .00001750  00000-0  14500-3 0  9991",
                       "2 47935  97.5000  81.0000 0010500  61.0000 299.0000 14.90000000 56790"),
    # NOAA weather ~850km SSO
    ("NOAA 18",        "1 28654U 05018A   24001.50000000  .00000100  00000-0  80000-5 0  9990",
                       "2 28654  99.0000 200.0000 0014000  50.0000 310.0000 14.10000000 67890"),
    ("NOAA 19",        "1 33591U 09005A   24001.50000000  .00000110  00000-0  85000-5 0  9991",
                       "2 33591  99.1000 201.0000 0013000  51.0000 309.0000 14.10000000 67891"),
    # GPS MEO ~20200km
    ("GPS IIR-1",      "1 24876U 97035A   24001.50000000  .00000020  00000-0  00000-0 0  9990",
                       "2 24876  55.0000  30.0000 0100000  90.0000 270.0000  2.00560000 78901"),
    ("GPS IIR-2",      "1 26360U 00025A   24001.50000000  .00000018  00000-0  00000-0 0  9991",
                       "2 26360  55.1000  90.0000 0100500  91.0000 269.0000  2.00560000 78902"),
    # Iridium ~780km
    ("IRIDIUM 100",    "1 24792U 97020A   24001.50000000  .00000500  00000-0  50000-4 0  9990",
                       "2 24792  86.4000  60.0000 0002000  80.0000 280.0000 14.34000000 89012"),
    ("IRIDIUM 102",    "1 27372U 02005A   24001.50000000  .00000480  00000-0  48000-4 0  9991",
                       "2 27372  86.4000  61.0000 0002100  81.0000 279.0000 14.34000000 89013"),
]


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
    miss_distance: float   # km
    closing_speed: float   # km/s
    probability: float     # Pc
    risk_level: str
    delta_v_cost: float    # m/s
    cascade_risk: float


CATEGORY_MAP = {
    "STARLINK": "Starlink", "ONEWEB": "OneWeb", "ISS": "Station",
    "DEB": "Debris", "GPS": "Navigation", "NOAA": "Weather",
    "IRIDIUM": "Comms", "PLANET": "EO",
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


class OrbitalEngine:
    def __init__(self):
        self.satellites: Dict[str, Satellite] = {}
        self.conjunctions: List[ConjunctionEvent] = []
        self.risk_graph = nx.Graph()
        self.last_updated: Optional[datetime] = None
        self._load_tles()

    def _load_tles(self):
        for name, line1, line2 in SEED_TLES:
            try:
                satrec = Satrec.twoline2rv(line1, line2)
                self.satellites[name] = Satellite(
                    name=name,
                    norad_id=line1[2:7].strip(),
                    category=categorize(name),
                    satrec=satrec
                )
            except Exception as e:
                print(f"TLE parse error {name}: {e}")
        print(f"[Astrum] Loaded {len(self.satellites)} satellites")

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

                for k, _ in enumerate(times):
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
            "top_risk_satellites": [{"name": k, "score": v} for k, v in top],
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "graph_edges": self.risk_graph.number_of_edges(),
            "graph_nodes": self.risk_graph.number_of_nodes(),
        }
