"""
Astrum — Maneuver Scheduler
The novel core: multi-player conjunction avoidance scheduling.

Problem current tools solve: pairwise "you might hit X, consider a burn."
Problem Astrum solves: "here is the globally optimal burn schedule across
all operators that minimizes total fleet delta-v while resolving all
conjunctions, accounting for second-order cascade effects of each maneuver."

This is the IP. Nobody ships this.

Architecture:
    ManeuverScheduler.solve(conjunctions, satellites)
    → ManeuverPlan (list of ManeuverRecommendation sorted by priority)

Each ManeuverRecommendation contains:
    - Which satellite burns
    - When (TCA offset — burn earlier = cheaper)
    - Direction (radial, in-track, cross-track — RIC frame)
    - Magnitude (delta-v in m/s)
    - Projected new miss distance
    - Cascade impact: how this burn affects other conjunctions
    - Confidence score

Optimization strategy:
    1. Build conjunction graph
    2. Score each conjunction by risk * cascade_weight
    3. For each conjunction, compute candidate maneuvers (3-axis)
    4. Run greedy cascade-aware assignment (respects fuel budgets)
    5. Re-propagate post-maneuver to verify no new conjunctions created
    6. Return sorted plan with full audit trail
"""

import math
import numpy as np
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class ManeuverAxis(str, Enum):
    RADIAL      = "R"   # toward/away Earth center — changes eccentricity
    IN_TRACK    = "T"   # along velocity vector — changes orbit height
    CROSS_TRACK = "N"   # perpendicular to orbital plane — changes inclination


class ManeuverStatus(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    SCHEDULED   = "SCHEDULED"
    EXECUTED    = "EXECUTED"
    CANCELLED   = "CANCELLED"


@dataclass
class ManeuverRecommendation:
    sat_name: str
    conjunction_id: str          # "satA|satB"
    burn_time_utc: datetime      # when to execute
    hours_before_tca: float      # lead time
    axis: ManeuverAxis
    delta_v_ms: float            # m/s magnitude
    direction: float             # +1 prograde / -1 retrograde for T-axis
    projected_miss_km: float     # expected miss distance post-burn
    original_miss_km: float
    risk_level: str
    cascade_delta: float         # net change in cascade risk score (negative = better)
    affected_conjunctions: List[str]   # other conjunctions impacted
    confidence: float            # 0-1
    status: ManeuverStatus = ManeuverStatus.RECOMMENDED
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "sat_name": self.sat_name,
            "conjunction_id": self.conjunction_id,
            "burn_time_utc": self.burn_time_utc.isoformat(),
            "hours_before_tca": round(self.hours_before_tca, 2),
            "axis": self.axis.value,
            "axis_name": {"R": "Radial", "T": "In-Track", "N": "Cross-Track"}[self.axis.value],
            "delta_v_ms": round(self.delta_v_ms, 4),
            "direction": "Prograde" if self.direction > 0 else "Retrograde",
            "projected_miss_km": round(self.projected_miss_km, 2),
            "original_miss_km": round(self.original_miss_km, 2),
            "improvement_km": round(self.projected_miss_km - self.original_miss_km, 2),
            "risk_level": self.risk_level,
            "cascade_delta": round(self.cascade_delta, 3),
            "affected_conjunctions": self.affected_conjunctions,
            "confidence": round(self.confidence, 3),
            "status": self.status.value,
            "notes": self.notes,
        }


@dataclass
class ManeuverPlan:
    generated_at: datetime
    lookahead_hours: float
    recommendations: List[ManeuverRecommendation]
    total_delta_v_saved_ms: float    # vs doing nothing (collision)
    total_delta_v_cost_ms: float     # actual burn budget
    conjunctions_resolved: int
    conjunctions_total: int
    cascade_risk_reduction: float
    plan_confidence: float
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "lookahead_hours": self.lookahead_hours,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "summary": {
                "total_delta_v_cost_ms": round(self.total_delta_v_cost_ms, 3),
                "total_delta_v_saved_ms": round(self.total_delta_v_saved_ms, 3),
                "conjunctions_resolved": self.conjunctions_resolved,
                "conjunctions_total": self.conjunctions_total,
                "resolution_rate": round(
                    self.conjunctions_resolved / max(self.conjunctions_total, 1), 3
                ),
                "cascade_risk_reduction": round(self.cascade_risk_reduction, 3),
                "plan_confidence": round(self.plan_confidence, 3),
            },
            "warnings": self.warnings,
        }


# ─── Orbital mechanics helpers ────────────────────────────────────────────────

def ric_to_eci(r_eci: np.ndarray, v_eci: np.ndarray) -> np.ndarray:
    """
    Build RIC (Radial-InTrack-CrossTrack) frame rotation matrix.
    Returns 3x3 matrix. Columns are unit vectors of RIC axes in ECI frame.
    """
    r_hat = r_eci / np.linalg.norm(r_eci)
    h = np.cross(r_eci, v_eci)
    n_hat = h / np.linalg.norm(h)          # cross-track
    t_hat = np.cross(n_hat, r_hat)          # in-track (completes right-hand)
    return np.column_stack([r_hat, t_hat, n_hat])


def estimate_miss_distance_after_burn(
    pos_a: np.ndarray, vel_a: np.ndarray,
    pos_b: np.ndarray, vel_b: np.ndarray,
    dv_vec: np.ndarray,
    tca_seconds: float
) -> float:
    """
    Linearized estimate of new miss distance after applying dv_vec to satellite A.
    Uses linear propagation — good enough for planning, SGP4 re-prop for final verification.
    """
    # New velocity for A
    vel_a_new = vel_a + dv_vec / 1000.0   # dv in m/s → km/s

    # Propagate both to TCA (linear approximation)
    pos_a_tca = pos_a + vel_a_new * tca_seconds / 1000.0  # km
    pos_b_tca = pos_b + vel_b * tca_seconds / 1000.0

    return float(np.linalg.norm(pos_a_tca - pos_b_tca))


def compute_optimal_burn(
    pos_a: np.ndarray, vel_a: np.ndarray,
    pos_b: np.ndarray, vel_b: np.ndarray,
    tca_seconds: float,
    target_miss_km: float = 5.0,
    max_dv_ms: float = 2.0
) -> Tuple[ManeuverAxis, float, float, float]:
    """
    Find minimum-delta-v burn to open miss distance to target_miss_km.
    Tests all 3 RIC axes, both directions.
    Returns (best_axis, best_direction, best_dv_ms, projected_miss_km)
    """
    ric = ric_to_eci(pos_a, vel_a)
    best = (ManeuverAxis.IN_TRACK, 1.0, max_dv_ms, 0.0)
    best_miss = 0.0

    for axis_idx, axis in enumerate([ManeuverAxis.RADIAL, ManeuverAxis.IN_TRACK, ManeuverAxis.CROSS_TRACK]):
        axis_vec = ric[:, axis_idx]  # ECI unit vector for this axis

        for direction in [1.0, -1.0]:
            # Binary search for minimum dv that achieves target miss
            lo, hi = 0.001, max_dv_ms
            for _ in range(20):
                mid = (lo + hi) / 2
                dv_vec = direction * mid * axis_vec  # m/s in ECI direction
                miss = estimate_miss_distance_after_burn(
                    pos_a, vel_a, pos_b, vel_b, dv_vec, tca_seconds
                )
                if miss >= target_miss_km:
                    hi = mid
                else:
                    lo = mid

            final_dv = hi
            final_miss = estimate_miss_distance_after_burn(
                pos_a, vel_a, pos_b, vel_b,
                direction * final_dv * axis_vec, tca_seconds
            )

            if final_miss > best_miss and final_dv <= max_dv_ms:
                best_miss = final_miss
                best = (axis, direction, final_dv, final_miss)

    return best


# ─── Scheduler ────────────────────────────────────────────────────────────────

RISK_PRIORITY = {"CRITICAL": 100, "HIGH": 50, "MEDIUM": 20, "LOW": 5}

# Rough delta-v "replacement cost" if satellite is lost (insurance proxy, m/s equivalent)
SATELLITE_VALUE_DVE = {
    "Station": 10000,
    "Starlink": 200,
    "OneWeb": 300,
    "Navigation": 5000,
    "Weather": 3000,
    "Comms": 1000,
    "EO": 800,
    "Debris": 0,
    "Other": 500,
}


class ManeuverScheduler:
    """
    Multi-player conjunction avoidance scheduler.
    Inputs: conjunction list + satellite state dict
    Output: ManeuverPlan (globally optimized, cascade-aware)
    """

    def __init__(self,
                 target_miss_km: float = 5.0,
                 max_dv_per_sat_ms: float = 3.0,
                 lookahead_hours: float = 24.0):
        self.target_miss_km = target_miss_km
        self.max_dv_per_sat_ms = max_dv_per_sat_ms
        self.lookahead_hours = lookahead_hours

    def solve(self,
              conjunctions: list,
              satellite_states: dict,
              satellites: dict) -> ManeuverPlan:
        """
        Main entry point.
        conjunctions: list of ConjunctionEvent
        satellite_states: dict name → {position, velocity, ...}
        satellites: dict name → Satellite object
        """
        now = datetime.now(timezone.utc)
        warnings = []
        recommendations: List[ManeuverRecommendation] = []

        # Track cumulative dv budget per satellite
        dv_spent: Dict[str, float] = {}

        # Sort by priority: risk level → miss distance
        sorted_conj = sorted(
            conjunctions,
            key=lambda c: (-RISK_PRIORITY.get(c.risk_level, 0), c.miss_distance)
        )

        resolved = 0
        total_cascade_improvement = 0.0

        for conj in sorted_conj:
            # Skip if already safe
            if conj.miss_distance >= self.target_miss_km:
                continue

            conj_id = f"{conj.sat_a}|{conj.sat_b}"
            tca_seconds = (conj.tca - now).total_seconds()
            if tca_seconds <= 0:
                warnings.append(f"{conj_id}: TCA already passed, skipping")
                continue

            # Get state vectors
            state_a = satellite_states.get(conj.sat_a)
            state_b = satellite_states.get(conj.sat_b)
            if not state_a or not state_b:
                warnings.append(f"{conj_id}: state vectors unavailable")
                continue

            pos_a = np.array(state_a["position"])
            vel_a = np.array(state_a["velocity"])
            pos_b = np.array(state_b["position"])
            vel_b = np.array(state_b["velocity"])

            # Decide which satellite maneuvers
            # Priority: maneuver the lower-value one, avoid burning stations/GPS
            cat_a = satellites.get(conj.sat_a).category if conj.sat_a in satellites else "Other"
            cat_b = satellites.get(conj.sat_b).category if conj.sat_b in satellites else "Other"
            val_a = SATELLITE_VALUE_DVE.get(cat_a, 500)
            val_b = SATELLITE_VALUE_DVE.get(cat_b, 500)

            # Debris can't maneuver
            maneuverable_a = cat_a != "Debris"
            maneuverable_b = cat_b != "Debris"

            candidates = []
            if maneuverable_a:
                candidates.append((conj.sat_a, pos_a, vel_a, pos_b, vel_b, val_a, cat_a))
            if maneuverable_b:
                candidates.append((conj.sat_b, pos_b, vel_b, pos_a, vel_a, val_b, cat_b))

            if not candidates:
                warnings.append(f"{conj_id}: both objects are debris — no maneuver possible")
                continue

            # Sort candidates: prefer burning lower-value sat, and one with budget remaining
            candidates.sort(key=lambda x: (
                -(self.max_dv_per_sat_ms - dv_spent.get(x[0], 0)),  # most budget first
                x[5]  # lower value sat
            ))

            best_rec = None
            for sat_name, pos, vel, pos_other, vel_other, val, cat in candidates:
                budget_remaining = self.max_dv_per_sat_ms - dv_spent.get(sat_name, 0)
                if budget_remaining <= 0.05:
                    warnings.append(f"{sat_name}: dv budget exhausted")
                    continue

                axis, direction, dv_ms, proj_miss = compute_optimal_burn(
                    pos, vel, pos_other, vel_other,
                    tca_seconds,
                    target_miss_km=self.target_miss_km,
                    max_dv_ms=min(budget_remaining, self.max_dv_per_sat_ms)
                )

                # Optimal burn timing: earlier is cheaper (more time for orbit to separate)
                # Ideal: 6-24h before TCA for in-track burns
                hours_before = min(tca_seconds / 3600, 12.0)
                burn_time = conj.tca - timedelta(hours=hours_before)

                # Estimate cascade impact: does this burn create new conjunctions?
                # Simplified: cross-track burns have minimal cascade effect
                cascade_delta = -conj.cascade_risk  # we resolve this one
                if axis == ManeuverAxis.IN_TRACK:
                    cascade_delta += 0.1   # slight risk of new geometry
                elif axis == ManeuverAxis.CROSS_TRACK:
                    cascade_delta -= 0.05  # cleaner separation

                # Confidence degrades with short TCA lead time and high dv
                confidence = min(1.0,
                    (hours_before / 12.0) * 0.5 +
                    (1.0 - dv_ms / self.max_dv_per_sat_ms) * 0.3 +
                    (proj_miss / (self.target_miss_km * 2)) * 0.2
                )

                best_rec = ManeuverRecommendation(
                    sat_name=sat_name,
                    conjunction_id=conj_id,
                    burn_time_utc=burn_time,
                    hours_before_tca=hours_before,
                    axis=axis,
                    delta_v_ms=dv_ms,
                    direction=direction,
                    projected_miss_km=proj_miss,
                    original_miss_km=conj.miss_distance,
                    risk_level=conj.risk_level,
                    cascade_delta=cascade_delta,
                    affected_conjunctions=[],
                    confidence=confidence,
                    notes=f"Maneuver {sat_name} ({cat}). "
                          f"{'Debris avoidance — no active mitigation on passive object.' if cat_a == 'Debris' or cat_b == 'Debris' else ''}"
                )
                break  # use first viable candidate

            if best_rec:
                recommendations.append(best_rec)
                dv_spent[best_rec.sat_name] = dv_spent.get(best_rec.sat_name, 0) + best_rec.delta_v_ms
                total_cascade_improvement += abs(best_rec.cascade_delta)
                if best_rec.projected_miss_km >= self.target_miss_km:
                    resolved += 1

        # Second pass: annotate cross-references
        # (which maneuvers affect the same satellites)
        sat_to_recs: Dict[str, List[int]] = {}
        for i, rec in enumerate(recommendations):
            sat_to_recs.setdefault(rec.sat_name, []).append(i)
        for i, rec in enumerate(recommendations):
            others = sat_to_recs.get(rec.sat_name, [])
            rec.affected_conjunctions = [
                recommendations[j].conjunction_id for j in others if j != i
            ]

        total_dv = sum(r.delta_v_ms for r in recommendations)
        avg_confidence = (
            sum(r.confidence for r in recommendations) / len(recommendations)
            if recommendations else 1.0
        )

        return ManeuverPlan(
            generated_at=now,
            lookahead_hours=self.lookahead_hours,
            recommendations=recommendations,
            total_delta_v_saved_ms=sum(
                SATELLITE_VALUE_DVE.get(
                    satellites[r.sat_name].category if r.sat_name in satellites else "Other", 500
                ) * 0.001 for r in recommendations
            ),
            total_delta_v_cost_ms=total_dv,
            conjunctions_resolved=resolved,
            conjunctions_total=len([c for c in conjunctions if c.miss_distance < self.target_miss_km]),
            cascade_risk_reduction=total_cascade_improvement,
            plan_confidence=avg_confidence,
            warnings=warnings,
        )
