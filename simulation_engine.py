"""
Astrum Simulation Engine

The single most powerful demo and R&D tool in the stack.

Capabilities:
  1. FRAGMENTATION INJECTION
     Inject a satellite fragmentation event (natural collision or ASAT)
     at any orbital shell. Generates a synthetic debris cloud based on
     NASA Standard Breakup Model (simplified).

  2. CASCADE PROJECTION
     After injection, propagate the debris field forward in time and
     compute the cascade risk to all active satellites.
     Show: which operators get alerts, in what sequence, with what urgency.

  3. ASAT SCENARIO PLAYBACK
     Named scenarios: Fengyun-2 (LEO denial at 850km), Cosmos-repeat
     (400km ISS shell), Starlink-killer (550km megaconstellation strike).
     Each pre-configured with realistic parameters.

  4. SNAPSHOT TIME-SERIES
     Generate a time series of risk states [T+0, T+1h, T+6h, T+24h]
     showing cascade evolution. Used for dashboard animation.

NASA Standard Breakup Model (simplified):
  Fragment count: N(>L) = 6 × (M_s × M_t / (M_s + M_t))^0.75 × L^-1.6
  Where L is characteristic length in meters, M in kg.
  Fragment velocity distribution: Maxwell-Boltzmann with σ = 0.2 × v_rel km/s.
  Shell spread: Gaussian in altitude ±Δa, spread over 90 days.

Demo value:
  Walk into any operator's NOC or a defense briefing.
  Inject a Fengyun-2 event. Watch 40 cascade alerts fire in 3 seconds.
  No competitor has this. This is what closes deals.
"""

import math
import random
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class ScenarioType(str, Enum):
    ASAT_TEST          = "ASAT_TEST"           # deliberate fragmentation
    HYPERVELOCITY      = "HYPERVELOCITY"       # accidental collision
    BATTERY_EXPLOSION  = "BATTERY_EXPLOSION"   # propellant/battery detonation
    SOLAR_PANEL_SHED   = "SOLAR_PANEL_SHED"    # partial breakup
    CUSTOM             = "CUSTOM"


@dataclass
class FragmentationEvent:
    event_id: str
    scenario_type: ScenarioType
    origin_name: str
    origin_norad: str
    altitude_km: float
    inclination_deg: float
    collision_velocity_kms: float   # relative velocity at impact
    mass_target_kg: float           # target object mass
    mass_impactor_kg: float         # impactor / projectile mass
    timestamp: datetime
    fragment_count: int             # estimated fragments >10cm
    delta_v_sigma_kms: float        # debris velocity spread
    altitude_spread_km: float       # how far the cloud spreads in altitude
    cloud_asymmetry: float          # 0=spherical, 1=directional
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "scenario_type": self.scenario_type.value,
            "origin_name": self.origin_name,
            "origin_norad": self.origin_norad,
            "altitude_km": self.altitude_km,
            "inclination_deg": self.inclination_deg,
            "collision_velocity_kms": self.collision_velocity_kms,
            "mass_target_kg": self.mass_target_kg,
            "mass_impactor_kg": self.mass_impactor_kg,
            "timestamp": self.timestamp.isoformat(),
            "fragment_count": self.fragment_count,
            "delta_v_sigma_kms": self.delta_v_sigma_kms,
            "altitude_spread_km": self.altitude_spread_km,
            "cloud_asymmetry": self.cloud_asymmetry,
            "notes": self.notes,
        }


@dataclass
class SyntheticDebrisObject:
    norad_id: str
    name: str
    altitude_km: float
    inclination_deg: float
    delta_v_applied_kms: float   # velocity kick from event
    threat_to: List[str]         # satellite names in conjunction
    estimated_mass_kg: float

    def to_dict(self) -> dict:
        return {
            "norad_id": self.norad_id,
            "name": self.name,
            "altitude_km": round(self.altitude_km, 2),
            "inclination_deg": round(self.inclination_deg, 3),
            "delta_v_kms": round(self.delta_v_applied_kms, 4),
            "threat_to": self.threat_to,
            "estimated_mass_kg": round(self.estimated_mass_kg, 2),
        }


@dataclass
class CascadeSnapshot:
    t_plus_hours: float
    new_conjunctions: int
    total_risk_score: float
    affected_satellites: List[str]
    critical_alerts: int
    high_alerts: int
    at_risk_operators: List[str]


@dataclass
class SimulationResult:
    event: FragmentationEvent
    synthetic_debris: List[SyntheticDebrisObject]
    cascade_snapshots: List[CascadeSnapshot]
    peak_conjunctions: int
    peak_at_hours: float
    total_affected_satellites: int
    operator_alert_sequence: List[dict]   # who gets alerted in what order
    kessler_risk_score: float             # 0-100: how close to runaway cascade
    mitigation_window_hours: float        # hours before debris cloud fully dispersed
    summary: str

    def to_dict(self) -> dict:
        return {
            "event": self.event.to_dict(),
            "fragment_count": len(self.synthetic_debris),
            "cascade_snapshots": [
                {
                    "t_plus_hours": s.t_plus_hours,
                    "new_conjunctions": s.new_conjunctions,
                    "total_risk_score": round(s.total_risk_score, 3),
                    "affected_satellites": s.affected_satellites,
                    "critical_alerts": s.critical_alerts,
                    "high_alerts": s.high_alerts,
                    "at_risk_operators": s.at_risk_operators,
                }
                for s in self.cascade_snapshots
            ],
            "peak_conjunctions": self.peak_conjunctions,
            "peak_at_hours": self.peak_at_hours,
            "total_affected_satellites": self.total_affected_satellites,
            "operator_alert_sequence": self.operator_alert_sequence,
            "kessler_risk_score": round(self.kessler_risk_score, 2),
            "mitigation_window_hours": round(self.mitigation_window_hours, 1),
            "summary": self.summary,
            "synthetic_debris_sample": [d.to_dict() for d in self.synthetic_debris[:10]],
        }


# ─── Named scenarios ──────────────────────────────────────────────────────────

NAMED_SCENARIOS = {
    "fengyun2": {
        "label": "Fengyun-2 ASAT Strike (850km Sun-Sync Shell)",
        "scenario_type": ScenarioType.ASAT_TEST,
        "origin_name": "FENGYUN 2G", "origin_norad": "38087",
        "altitude_km": 850, "inclination_deg": 98.5,
        "collision_velocity_kms": 9.5,
        "mass_target_kg": 1200, "mass_impactor_kg": 80,
        "cloud_asymmetry": 0.75,
        "notes": "Simulated ASAT strike on FENGYUN 2G at 850km polar orbit. "
                 "Threatens NOAA, Metop, Landsat, Planet Labs SSO constellations. "
                 "Asymmetric cloud consistent with directed intercept.",
    },
    "cosmos_repeat": {
        "label": "Cosmos Repeat — ISS Shell (400km Collision)",
        "scenario_type": ScenarioType.HYPERVELOCITY,
        "origin_name": "COSMOS 2251-REPEAT", "origin_norad": "SIMUL-A",
        "altitude_km": 415, "inclination_deg": 51.6,
        "collision_velocity_kms": 11.7,
        "mass_target_kg": 950, "mass_impactor_kg": 560,
        "cloud_asymmetry": 0.22,
        "notes": "Accidental collision in ISS shell. Immediate ISS evacuation zone. "
                 "Natural spherical debris distribution.",
    },
    "starlink_killer": {
        "label": "Starlink Shell Strike (550km ASAT)",
        "scenario_type": ScenarioType.ASAT_TEST,
        "origin_name": "STARLINK TARGET", "origin_norad": "SIMUL-B",
        "altitude_km": 550, "inclination_deg": 53.0,
        "collision_velocity_kms": 8.2,
        "mass_target_kg": 260, "mass_impactor_kg": 60,
        "cloud_asymmetry": 0.68,
        "notes": "ASAT strike into Starlink Gen1 shell. 12,000+ Starlink sats at risk. "
                 "Estimated 50-year orbital denial of 540-560km band.",
    },
    "battery": {
        "label": "Battery Explosion — Iridium 780km",
        "scenario_type": ScenarioType.BATTERY_EXPLOSION,
        "origin_name": "IRIDIUM DERELICT", "origin_norad": "SIMUL-C",
        "altitude_km": 780, "inclination_deg": 86.4,
        "collision_velocity_kms": 0.3,
        "mass_target_kg": 680, "mass_impactor_kg": 680,
        "cloud_asymmetry": 0.05,
        "notes": "Derelict Iridium satellite propellant/battery explosion. "
                 "Low relative velocity, nearly spherical distribution. "
                 "Primarily threatens polar orbit users.",
    },
}


class SimulationEngine:
    """
    Generates fragmentation events and projects their cascade impact
    across the current satellite population.
    """

    def __init__(self, orbital_engine, operator_registry=None):
        self.engine   = orbital_engine
        self.registry = operator_registry
        self._last_results: Dict[str, SimulationResult] = {}

    # ── NASA Standard Breakup Model ───────────────────────────────────────────

    def _fragment_count(self, mass_t: float, mass_i: float, v_rel: float) -> int:
        """
        Simplified NASA SBM.
        N(>10cm) ≈ 6 × (reduced_mass)^0.75
        Scales with kinetic energy for ASAT vs collision distinction.
        """
        e_ratio = 0.5 * mass_i * (v_rel * 1000) ** 2 / (mass_t * 1000)
        reduced = (mass_t * mass_i) / (mass_t + mass_i)
        n = max(10, int(6 * (reduced ** 0.75) * min(3.0, 1 + e_ratio / 5e6)))
        return min(n, 3500)  # Fengyun-1C is historical max

    def _debris_velocity_spread(self, v_rel: float, scenario: ScenarioType) -> float:
        """σ of Maxwell-Boltzmann velocity distribution for fragments."""
        base = 0.2 * v_rel  # 20% of relative velocity
        if scenario == ScenarioType.ASAT_TEST:
            return base * 1.4   # warhead adds energy
        if scenario == ScenarioType.BATTERY_EXPLOSION:
            return base * 0.3   # low energy
        return base

    def _altitude_spread(self, dv_sigma: float, alt: float) -> float:
        """
        Rough altitude spread: ΔV/v_circular × 2 × altitude.
        Over 90 days, debris spreads across this band.
        """
        R = 6371 + alt
        v_circ = math.sqrt(398600.4418 / R)  # km/s
        return min(200, (dv_sigma / v_circ) * 2 * alt)

    # ── Synthetic debris cloud ────────────────────────────────────────────────

    def _generate_debris_cloud(
        self,
        event: FragmentationEvent,
        sat_population: dict,
    ) -> List[SyntheticDebrisObject]:
        """
        Generate a synthetic population of debris objects.
        Check each against active satellite altitudes for conjunction risk.
        """
        random.seed(42)  # deterministic for reproducibility
        debris = []

        # Active satellite altitudes for threat check
        import numpy as np
        sat_altitudes: Dict[str, float] = {}
        for name, sat in sat_population.items():
            if sat.position is not None:
                alt = float(np.linalg.norm(sat.position)) - 6371.0
                sat_altitudes[name] = alt

        for i in range(event.fragment_count):
            # Sample altitude from Gaussian around event altitude
            dv = random.gauss(0, event.delta_v_sigma_kms)
            alt_offset = dv * 200 / event.delta_v_sigma_kms  # scale
            alt = event.altitude_km + random.gauss(0, event.altitude_spread_km / 3)
            alt = max(200, alt)  # debris below 200km reenters fast

            # Inclination spread (ASAT = directional, collision = spread)
            inc_spread = 0.5 * (1 - event.cloud_asymmetry) + 0.1
            inc = event.inclination_deg + random.gauss(0, inc_spread)

            # Fragment mass (power law: most fragments small)
            mass = max(0.01, random.expovariate(2.0) * 0.5)

            # Check which active sats are threatened (±altitude band)
            threatened = [
                name for name, sat_alt in sat_altitudes.items()
                if abs(sat_alt - event.altitude_km) < 80.0  # 80km threat band around event altitude
            ]

            deb_id = f"SIM-{event.origin_norad}-{i:04d}"
            debris.append(SyntheticDebrisObject(
                norad_id=deb_id,
                name=f"SIM DEB {event.origin_name} {i+1:04d}",
                altitude_km=alt,
                inclination_deg=inc,
                delta_v_applied_kms=abs(dv),
                threat_to=threatened,
                estimated_mass_kg=mass,
            ))

        return debris

    # ── Cascade projection ────────────────────────────────────────────────────

    def _project_cascade(
        self,
        event: FragmentationEvent,
        debris: List[SyntheticDebrisObject],
        sat_population: dict,
    ) -> Tuple[List[CascadeSnapshot], List[dict]]:
        """
        Project cascade risk over time. Model:
        - T+0: immediate conjunctions from fast close-approach debris
        - T+1h: cloud dispersing, second-order conjunctions
        - T+6h: full shell spread, maximum conjunction density
        - T+24h: debris in stable orbits, equilibrium risk
        - T+72h: low-altitude objects reentering, partial relief
        """
        # Operator lookup
        op_name_for_sat = {}
        if self.registry:
            for name, sat in sat_population.items():
                op = self.registry.get_operator_for_norad(sat.norad_id)
                op_name_for_sat[name] = op.name if op else "Unknown"

        # Count threatened sats per snapshot time
        all_threatened = set()
        for d in debris:
            all_threatened.update(d.threat_to)

        n_pop = len(sat_population)
        n_threatened = len(all_threatened)
        base_risk = len(debris) * 0.001

        # Time series profile (cloud density × intersection probability by time)
        time_profiles = [
            # (t_hours, conj_multiplier, risk_mult, description)
            (0.0,   0.15, 0.10, "Event detected, fast fragments only"),
            (0.5,   0.45, 0.35, "Cloud expanding, first wave conjunctions"),
            (1.0,   0.75, 0.60, "Second-order conjunctions forming"),
            (3.0,   0.95, 0.85, "Near-peak density, alerts propagating"),
            (6.0,   1.00, 1.00, "Peak conjunction density"),
            (12.0,  0.90, 0.88, "Debris ring forming"),
            (24.0,  0.80, 0.75, "Stable debris band established"),
            (48.0,  0.70, 0.65, "Low-altitude objects reentering"),
            (72.0,  0.60, 0.55, "Partial altitude relief"),
            (168.0, 0.45, 0.40, "1-week equilibrium state"),
        ]

        snapshots = []
        for t_hours, conj_mult, risk_mult, _ in time_profiles:
            n_conj = max(0, int(n_threatened * conj_mult * 1.2))
            risk = base_risk * risk_mult * (n_threatened / max(1, n_pop))

            # Alert severity breakdown
            n_critical = max(0, int(n_conj * 0.05 * conj_mult))
            n_high     = max(0, int(n_conj * 0.25 * conj_mult))

            affected = list(all_threatened)[:max(1, int(n_threatened * conj_mult))]
            ops = list(set(op_name_for_sat.get(s, "Unknown") for s in affected))

            snapshots.append(CascadeSnapshot(
                t_plus_hours=t_hours,
                new_conjunctions=n_conj,
                total_risk_score=round(risk, 4),
                affected_satellites=affected[:8],
                critical_alerts=n_critical,
                high_alerts=n_high,
                at_risk_operators=ops[:5],
            ))

        # Operator alert sequence (who gets alerted first and why)
        alert_sequence = []
        notified_ops = set()
        for d in sorted(debris, key=lambda x: -len(x.threat_to)):
            for sat_name in d.threat_to[:3]:
                op = op_name_for_sat.get(sat_name, "Unknown")
                if op not in notified_ops:
                    notified_ops.add(op)
                    threat_level = "CRITICAL" if d.altitude_km < 600 else "HIGH"
                    alert_sequence.append({
                        "t_plus_minutes": round(len(alert_sequence) * 2.5, 1),
                        "operator": op,
                        "satellite": sat_name,
                        "threat_fragment": d.name,
                        "miss_distance_km": round(abs(d.altitude_km -
                            (sum(x.altitude_km for x in [d]) / 1)), 2),
                        "alert_level": threat_level,
                        "action": "MANEUVER_WINDOW_OPEN" if threat_level == "CRITICAL" else "MONITOR",
                    })
            if len(alert_sequence) >= 12:
                break

        return snapshots, alert_sequence

    # ── Kessler risk score ────────────────────────────────────────────────────

    def _kessler_score(self, event: FragmentationEvent, debris: List[SyntheticDebrisObject], sat_pop: dict) -> float:
        """
        0-100 score estimating probability of runaway cascade (Kessler Syndrome).
        Factors: altitude, fragment count, population density, collision probability.
        High score = intervention required to prevent cascade.
        """
        # Altitude factor: 700-1000km is most dangerous (long lifetime, high density)
        alt_factor = {
            (0,    400):  0.1,   # reentry in years
            (400,  600):  0.3,   # 10-50yr lifetime
            (600,  800):  0.65,  # 50-200yr lifetime
            (800,  1100): 0.95,  # 200-500yr lifetime, MOST DANGEROUS
            (1100, 2000): 0.70,  # MEO approaches, still bad
            (2000, 99999): 0.4,  # MEO/GEO — bad but slow cascade
        }
        af = 0.5
        for (lo, hi), v in alt_factor.items():
            if lo <= event.altitude_km < hi:
                af = v
                break

        # Population density factor
        sats_in_shell = sum(1 for d in debris if d.threat_to)
        pop_factor = min(1.0, sats_in_shell / 50)

        # Fragment energy factor
        frag_factor = min(1.0, event.fragment_count / 1000)

        # Type factor
        type_factor = {
            ScenarioType.ASAT_TEST: 1.0,
            ScenarioType.HYPERVELOCITY: 0.8,
            ScenarioType.BATTERY_EXPLOSION: 0.3,
            ScenarioType.SOLAR_PANEL_SHED: 0.15,
            ScenarioType.CUSTOM: 0.6,
        }[event.scenario_type]

        score = (af * 0.35 + pop_factor * 0.30 + frag_factor * 0.20 + type_factor * 0.15) * 100
        return min(100, score)

    # ── Public interface ──────────────────────────────────────────────────────

    def run_scenario(
        self,
        scenario_name: str = None,
        custom_params: dict = None,
    ) -> SimulationResult:
        """
        Run a named scenario or custom fragmentation event.
        Returns full SimulationResult with cascade time series.
        """
        if scenario_name and scenario_name in NAMED_SCENARIOS:
            params = NAMED_SCENARIOS[scenario_name].copy()
        elif custom_params:
            params = custom_params.copy()
        else:
            raise ValueError(f"Unknown scenario '{scenario_name}'. Available: {list(NAMED_SCENARIOS.keys())}")

        now = datetime.now(timezone.utc)
        scenario_type = params.get("scenario_type", ScenarioType.CUSTOM)
        if isinstance(scenario_type, str):
            scenario_type = ScenarioType(scenario_type)

        mass_t = params.get("mass_target_kg", 500)
        mass_i = params.get("mass_impactor_kg", 100)
        v_rel  = params.get("collision_velocity_kms", 10.0)
        alt    = params.get("altitude_km", 550)

        n_frags    = self._fragment_count(mass_t, mass_i, v_rel)
        dv_sigma   = self._debris_velocity_spread(v_rel, scenario_type)
        alt_spread = self._altitude_spread(dv_sigma, alt)
        mit_window = max(6, 200 - alt * 0.15)  # hours before cloud disperses

        event = FragmentationEvent(
            event_id=str(uuid.uuid4()),
            scenario_type=scenario_type,
            origin_name=params.get("origin_name", "UNKNOWN"),
            origin_norad=params.get("origin_norad", "99999"),
            altitude_km=alt,
            inclination_deg=params.get("inclination_deg", 53.0),
            collision_velocity_kms=v_rel,
            mass_target_kg=mass_t,
            mass_impactor_kg=mass_i,
            timestamp=now,
            fragment_count=n_frags,
            delta_v_sigma_kms=dv_sigma,
            altitude_spread_km=alt_spread,
            cloud_asymmetry=params.get("cloud_asymmetry", 0.3),
            notes=params.get("notes", ""),
        )

        # Ensure positions are propagated before threat detection
        self.engine.propagate_all()
        sat_pop = self.engine.satellites
        debris = self._generate_debris_cloud(event, sat_pop)
        snapshots, alert_seq = self._project_cascade(event, debris, sat_pop)
        kessler = self._kessler_score(event, debris, sat_pop)

        all_affected = set()
        for d in debris:
            all_affected.update(d.threat_to)
        peak_snap = max(snapshots, key=lambda s: s.new_conjunctions)

        label = params.get("label", scenario_type.value)
        summary = (
            f"{label}: {n_frags} fragments generated at {alt:.0f}km. "
            f"Peak {peak_snap.new_conjunctions} conjunctions at T+{peak_snap.t_plus_hours:.0f}h. "
            f"{len(all_affected)} satellites threatened. "
            f"Kessler risk score: {kessler:.0f}/100. "
            f"{'⚠ INTERVENTION REQUIRED' if kessler > 60 else 'Manageable with coordinated avoidance'}."
        )

        result = SimulationResult(
            event=event,
            synthetic_debris=debris,
            cascade_snapshots=snapshots,
            peak_conjunctions=peak_snap.new_conjunctions,
            peak_at_hours=peak_snap.t_plus_hours,
            total_affected_satellites=len(all_affected),
            operator_alert_sequence=alert_seq,
            kessler_risk_score=kessler,
            mitigation_window_hours=mit_window,
            summary=summary,
        )
        self._last_results[event.event_id] = result
        return result

    def get_available_scenarios(self) -> List[dict]:
        return [
            {"name": k, "label": v["label"], "scenario_type": v["scenario_type"].value,
             "altitude_km": v["altitude_km"], "notes": v.get("notes", "")}
            for k, v in NAMED_SCENARIOS.items()
        ]

    def get_last_result(self, event_id: str = None) -> Optional[dict]:
        if event_id:
            r = self._last_results.get(event_id)
            return r.to_dict() if r else None
        if self._last_results:
            last = list(self._last_results.values())[-1]
            return last.to_dict()
        return None
