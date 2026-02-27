"""
Astrum — Alert & Remediation Engine

Generates structured, cryptographically-signable alerts for:
  1. CONJUNCTION ALERTS   — active satellite at risk, recommended avoidance window
  2. DEBRIS FIELD ALERTS  — fragmentation event or high-density region detected
  3. THREAT ALERTS        — debris pattern consistent with deliberate seeding
  4. REMEDIATION TARGETS  — ranked intercept candidates for active debris removal vehicles

Alert lifecycle:
    ACTIVE → ACKNOWLEDGED → RESOLVED / EXPIRED

Payload is structured JSON designed to be signed with Ed25519 (beacon protocol Layer 1).
Every alert carries a hash chain reference so downstream consumers can verify
they received the same alert that was broadcast.

Threat attribution model:
    Natural fragmentation:
      - Spherically symmetric debris cloud expansion
      - Single origin point, decaying eccentricity distribution
      - Consistent with known object age/material

    Deliberate seeding indicators:
      - Asymmetric cloud geometry (targeted shell/inclination)
      - Multiple origin vectors
      - Fragment mass distribution inconsistent with collision physics
      - Timing correlation with geopolitical events (flagged for human review)

    Astrum does NOT make final attribution calls — it scores and routes to operators.
    Military/intelligence consumers license the raw scoring data.
"""

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


# ─── Enums ────────────────────────────────────────────────────────────────────

class AlertType(str, Enum):
    CONJUNCTION        = "CONJUNCTION"
    DEBRIS_FIELD       = "DEBRIS_FIELD"
    FRAGMENTATION      = "FRAGMENTATION"
    THREAT_ASSESSMENT  = "THREAT_ASSESSMENT"
    REMEDIATION_TARGET = "REMEDIATION_TARGET"

class AlertSeverity(str, Enum):
    CRITICAL = "CRITICAL"   # immediate action required (<2h TCA or Pc > 1e-4)
    HIGH     = "HIGH"       # action within 6h
    MEDIUM   = "MEDIUM"     # awareness, plan review
    INFO     = "INFO"       # situational awareness only

class AlertStatus(str, Enum):
    ACTIVE       = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED     = "RESOLVED"
    EXPIRED      = "EXPIRED"

class ActionType(str, Enum):
    AVOIDANCE_MANEUVER  = "AVOIDANCE_MANEUVER"
    MONITOR             = "MONITOR"
    REMEDIATION_TASK    = "REMEDIATION_TASK"
    THREAT_REVIEW       = "THREAT_REVIEW"
    NO_ACTION           = "NO_ACTION"

class ThreatIndicator(str, Enum):
    NONE          = "NONE"
    LOW           = "LOW"          # anomalous but explainable
    MODERATE      = "MODERATE"     # multiple indicators, review recommended
    HIGH          = "HIGH"         # pattern consistent with deliberate action
    CONFIRMED     = "CONFIRMED"    # human-confirmed (not auto-set)


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class AvoidanceWindow:
    """Recommended burn window for conjunction avoidance."""
    open_utc: datetime
    close_utc: datetime
    recommended_burn_utc: datetime
    axis: str                # R/T/N
    direction: str           # Prograde / Retrograde
    delta_v_ms: float
    projected_miss_km: float
    confidence: float

    def to_dict(self) -> dict:
        return {
            "open_utc": self.open_utc.isoformat(),
            "close_utc": self.close_utc.isoformat(),
            "recommended_burn_utc": self.recommended_burn_utc.isoformat(),
            "axis": self.axis,
            "direction": self.direction,
            "delta_v_ms": round(self.delta_v_ms, 4),
            "projected_miss_km": round(self.projected_miss_km, 2),
            "confidence": round(self.confidence, 3),
        }


@dataclass
class RemediationSpec:
    """Intercept parameters for an active debris removal vehicle."""
    target_norad: str
    target_name: str
    target_category: str           # Debris subtype
    rendezvous_delta_v_ms: float   # estimated ΔV to intercept
    intercept_window_open: datetime
    intercept_window_close: datetime
    priority_score: float          # 0-100, higher = intercept sooner
    cascade_multiplier: float      # how many secondary conjunctions this object drives
    threat_indicator: ThreatIndicator
    recommended_action: str        # CAPTURE / DEORBIT / REDIRECT / MONITOR

    def to_dict(self) -> dict:
        return {
            "target_norad": self.target_norad,
            "target_name": self.target_name,
            "target_category": self.target_category,
            "rendezvous_delta_v_ms": round(self.rendezvous_delta_v_ms, 2),
            "intercept_window_open": self.intercept_window_open.isoformat(),
            "intercept_window_close": self.intercept_window_close.isoformat(),
            "priority_score": round(self.priority_score, 2),
            "cascade_multiplier": round(self.cascade_multiplier, 3),
            "threat_indicator": self.threat_indicator.value,
            "recommended_action": self.recommended_action,
        }


@dataclass
class ThreatAssessment:
    """Debris field threat scoring."""
    field_id: str                       # hash of origin object + epoch
    origin_object: str
    fragment_count_estimated: int
    shell_altitude_km: float
    shell_inclination_deg: float
    cloud_asymmetry_score: float        # 0=spherical(natural), 1=highly directional
    mass_distribution_anomaly: float    # 0=normal, 1=anomalous
    timing_correlation_score: float     # 0=none, 1=high correlation with events
    composite_threat_score: float       # weighted composite
    indicator: ThreatIndicator
    notes: str

    def to_dict(self) -> dict:
        return {
            "field_id": self.field_id,
            "origin_object": self.origin_object,
            "fragment_count_estimated": self.fragment_count_estimated,
            "shell_altitude_km": round(self.shell_altitude_km, 1),
            "shell_inclination_deg": round(self.shell_inclination_deg, 2),
            "cloud_asymmetry_score": round(self.cloud_asymmetry_score, 3),
            "mass_distribution_anomaly": round(self.mass_distribution_anomaly, 3),
            "timing_correlation_score": round(self.timing_correlation_score, 3),
            "composite_threat_score": round(self.composite_threat_score, 3),
            "indicator": self.indicator.value,
            "notes": self.notes,
        }


@dataclass
class AstrumAlert:
    """
    A single Astrum alert. Designed for signed broadcast.
    The `payload_hash` field is SHA-256 of the canonical JSON payload —
    consumers verify this to confirm they received the authentic alert.
    """
    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    generated_at: datetime
    expires_at: datetime
    title: str
    summary: str

    # Routing
    affected_satellites: List[str]
    affected_operators: List[str]        # inferred from satellite category/catalog
    affected_shell_km: Optional[float]

    # Recommended action
    action_type: ActionType
    action_deadline_utc: Optional[datetime]

    # Type-specific payloads
    avoidance_window: Optional[AvoidanceWindow] = None
    remediation_spec: Optional[RemediationSpec] = None
    threat_assessment: Optional[ThreatAssessment] = None

    # Integrity
    payload_hash: str = ""
    sequence_number: int = 0

    def _canonical_payload(self) -> dict:
        """Deterministic dict for hashing — excludes payload_hash itself."""
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "generated_at": self.generated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "title": self.title,
            "summary": self.summary,
            "affected_satellites": sorted(self.affected_satellites),
            "affected_operators": sorted(self.affected_operators),
            "action_type": self.action_type.value,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(self._canonical_payload(), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def finalize(self):
        """Call after construction to set hash."""
        self.payload_hash = self.compute_hash()

    def to_dict(self) -> dict:
        d = {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "status": self.status.value,
            "generated_at": self.generated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "title": self.title,
            "summary": self.summary,
            "affected_satellites": self.affected_satellites,
            "affected_operators": self.affected_operators,
            "affected_shell_km": self.affected_shell_km,
            "action_type": self.action_type.value,
            "action_deadline_utc": self.action_deadline_utc.isoformat() if self.action_deadline_utc else None,
            "sequence_number": self.sequence_number,
            "payload_hash": self.payload_hash,
        }
        if self.avoidance_window:
            d["avoidance_window"] = self.avoidance_window.to_dict()
        if self.remediation_spec:
            d["remediation_spec"] = self.remediation_spec.to_dict()
        if self.threat_assessment:
            d["threat_assessment"] = self.threat_assessment.to_dict()
        return d


# ─── Operator routing table ───────────────────────────────────────────────────
# In production: loaded from operator registry (API or database)
# Maps satellite category → known operators with contact/webhook endpoints

OPERATOR_REGISTRY = {
    "Starlink":   ["SpaceX-FlightOps", "SpaceX-Safety"],
    "OneWeb":     ["OneWeb-Operations"],
    "Station":    ["NASA-JSC", "Roscosmos-MCC", "ESA-ESOC"],
    "Navigation": ["USAF-GPS", "Roscosmos-GLONASS", "ESA-Galileo"],
    "Weather":    ["NOAA-SatOps", "EUMETSAT"],
    "EO":         ["PlanetLabs-Ops"],
    "Comms":      ["Iridium-NEXT"],
    "Debris":     ["USSPACECOM", "ESA-SST", "JAXA-SSA"],
    "Other":      ["USSPACECOM"],
}

# Debris fields with known threat history
KNOWN_THREAT_EVENTS = [
    {"name": "FENGYUN 1C", "year": 2007, "type": "ASAT_TEST",
     "fragments": 3500, "indicator": ThreatIndicator.HIGH},
    {"name": "COSMOS 2251", "year": 2009, "type": "COLLISION",
     "fragments": 2000, "indicator": ThreatIndicator.LOW},
    {"name": "IRIDIUM 33",  "year": 2009, "type": "COLLISION",
     "fragments": 600,  "indicator": ThreatIndicator.NONE},
]


def _infer_operators(categories: List[str]) -> List[str]:
    ops = set()
    for cat in categories:
        ops.update(OPERATOR_REGISTRY.get(cat, ["USSPACECOM"]))
    return sorted(ops)


def _severity_from_risk(risk_level: str, tca_hours: float) -> AlertSeverity:
    if risk_level == "CRITICAL" or tca_hours < 2:
        return AlertSeverity.CRITICAL
    if risk_level == "HIGH" or tca_hours < 6:
        return AlertSeverity.HIGH
    if risk_level == "MEDIUM":
        return AlertSeverity.MEDIUM
    return AlertSeverity.INFO


def _threat_indicator_from_scores(asymmetry: float, mass_anom: float, timing: float) -> ThreatIndicator:
    composite = asymmetry * 0.4 + mass_anom * 0.35 + timing * 0.25
    if composite < 0.15:  return ThreatIndicator.NONE
    if composite < 0.35:  return ThreatIndicator.LOW
    if composite < 0.60:  return ThreatIndicator.MODERATE
    return ThreatIndicator.HIGH


# ─── Alert Engine ─────────────────────────────────────────────────────────────

class AlertEngine:
    """
    Generates and manages the full alert lifecycle for Astrum.
    Consumes output from OrbitalEngine + ManeuverScheduler.
    """

    def __init__(self):
        self._alerts: Dict[str, AstrumAlert] = {}
        self._seq: int = 0
        self._remediation_targets: List[RemediationSpec] = []

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _make_alert(self, **kwargs) -> AstrumAlert:
        alert = AstrumAlert(
            alert_id=str(uuid.uuid4()),
            sequence_number=self._next_seq(),
            status=AlertStatus.ACTIVE,
            **kwargs
        )
        alert.finalize()
        self._alerts[alert.alert_id] = alert
        return alert

    # ── Conjunction alerts ────────────────────────────────────────────────────

    def generate_conjunction_alerts(
        self,
        conjunctions: list,
        maneuver_plan,
        satellites: dict,
    ) -> List[AstrumAlert]:
        """
        One alert per actionable conjunction.
        Includes avoidance window from maneuver plan if available.
        """
        now = datetime.now(timezone.utc)
        new_alerts = []

        # Map conjunction_id → maneuver recommendation
        plan_map = {}
        if maneuver_plan:
            for rec in maneuver_plan.recommendations:
                plan_map[rec.conjunction_id] = rec

        for c in conjunctions:
            if c.risk_level == "LOW" and c.miss_distance > 50:
                continue  # don't alert on distant LOW events

            tca_hours = (c.tca - now).total_seconds() / 3600
            if tca_hours < 0:
                continue

            severity = _severity_from_risk(c.risk_level, tca_hours)
            conj_id = f"{c.sat_a}|{c.sat_b}"

            # Get categories
            cat_a = satellites[c.sat_a].category if c.sat_a in satellites else "Other"
            cat_b = satellites[c.sat_b].category if c.sat_b in satellites else "Other"
            operators = _infer_operators([cat_a, cat_b])
            shell_km = None
            if c.sat_a in satellites and satellites[c.sat_a].position is not None:
                import numpy as np
                shell_km = round(float(np.linalg.norm(satellites[c.sat_a].position)) - 6371.0, 0)

            # Avoidance window from maneuver plan
            av_window = None
            rec = plan_map.get(conj_id)
            if rec:
                av_window = AvoidanceWindow(
                    open_utc=now,
                    close_utc=rec.burn_time_utc + timedelta(hours=2),
                    recommended_burn_utc=rec.burn_time_utc,
                    axis=rec.axis.value,
                    direction=rec.direction,
                    delta_v_ms=rec.delta_v_ms,
                    projected_miss_km=rec.projected_miss_km,
                    confidence=rec.confidence,
                )

            both_debris = cat_a == "Debris" and cat_b == "Debris"
            action = ActionType.NO_ACTION if both_debris else (
                ActionType.AVOIDANCE_MANEUVER if c.miss_distance < 10 else ActionType.MONITOR
            )
            deadline = c.tca - timedelta(hours=1) if action == ActionType.AVOIDANCE_MANEUVER else None

            title = f"{c.risk_level} CONJUNCTION: {c.sat_a} / {c.sat_b}"
            summary = (
                f"TCA in {tca_hours:.1f}h · Miss distance {c.miss_distance:.2f} km · "
                f"Pc {c.probability:.2e} · "
                f"{'No active avoidance possible (debris)' if both_debris else f'ΔV {c.delta_v_cost:.3f} m/s recommended'}"
            )

            alert = self._make_alert(
                alert_type=AlertType.CONJUNCTION,
                severity=severity,
                generated_at=now,
                expires_at=c.tca + timedelta(hours=1),
                title=title,
                summary=summary,
                affected_satellites=[c.sat_a, c.sat_b],
                affected_operators=operators,
                affected_shell_km=shell_km,
                action_type=action,
                action_deadline_utc=deadline,
                avoidance_window=av_window,
            )
            new_alerts.append(alert)

        return new_alerts

    # ── Debris field alerts ───────────────────────────────────────────────────

    def generate_debris_field_alerts(
        self,
        conjunctions: list,
        satellites: dict,
    ) -> List[AstrumAlert]:
        """
        Identify high-density debris regions and generate field-level alerts.
        Groups debris objects by shell and scores them for threat indicators.
        """
        now = datetime.now(timezone.utc)
        new_alerts = []

        # Find debris objects involved in multiple conjunctions
        import numpy as np
        from collections import defaultdict
        debris_involvement: Dict[str, int] = defaultdict(int)
        for c in conjunctions:
            for sat_name in [c.sat_a, c.sat_b]:
                if sat_name in satellites and satellites[sat_name].category == "Debris":
                    debris_involvement[sat_name] += 1

        # Score each active debris object for remediation + threat
        for deb_name, involvement_count in debris_involvement.items():
            sat = satellites[deb_name]
            if sat.position is None:
                continue

            altitude = float(np.linalg.norm(sat.position)) - 6371.0
            cascade_mult = involvement_count

            # Threat scoring — pattern analysis on known debris events
            asymmetry = 0.0
            mass_anom = 0.0
            timing_corr = 0.0

            for known in KNOWN_THREAT_EVENTS:
                if known["name"].upper() in deb_name.upper():
                    if known["type"] == "ASAT_TEST":
                        asymmetry = 0.72     # ASAT debris is directional
                        mass_anom = 0.68     # uniform fragmentation from warhead
                        timing_corr = 0.85   # coordinated with geopolitical event
                    elif known["type"] == "COLLISION":
                        asymmetry = 0.25
                        mass_anom = 0.20
                        timing_corr = 0.05
                    break

            composite = asymmetry * 0.4 + mass_anom * 0.35 + timing_corr * 0.25
            indicator = _threat_indicator_from_scores(asymmetry, mass_anom, timing_corr)

            # Build threat assessment
            field_id = hashlib.md5(f"{deb_name}{altitude:.0f}".encode()).hexdigest()[:12]
            threat = ThreatAssessment(
                field_id=field_id,
                origin_object=deb_name,
                fragment_count_estimated=max(1, involvement_count * 15),
                shell_altitude_km=altitude,
                shell_inclination_deg=0.0,  # would come from TLE parse in production
                cloud_asymmetry_score=asymmetry,
                mass_distribution_anomaly=mass_anom,
                timing_correlation_score=timing_corr,
                composite_threat_score=composite,
                indicator=indicator,
                notes=self._threat_notes(deb_name, indicator, asymmetry),
            )

            # Remediation spec
            rendezvous_dv = _estimate_rendezvous_dv(altitude)
            intercept_open = now + timedelta(hours=6)
            intercept_close = now + timedelta(hours=72)
            priority = min(100, cascade_mult * 20 + composite * 40)
            recommended = _recommended_action(altitude, composite, cascade_mult)

            rem_spec = RemediationSpec(
                target_norad=sat.norad_id,
                target_name=deb_name,
                target_category="Debris",
                rendezvous_delta_v_ms=rendezvous_dv,
                intercept_window_open=intercept_open,
                intercept_window_close=intercept_close,
                priority_score=priority,
                cascade_multiplier=float(cascade_mult),
                threat_indicator=indicator,
                recommended_action=recommended,
            )
            self._remediation_targets.append(rem_spec)

            # Severity
            if indicator in (ThreatIndicator.HIGH, ThreatIndicator.CONFIRMED):
                severity = AlertSeverity.HIGH
                alert_type = AlertType.THREAT_ASSESSMENT
            elif cascade_mult >= 3:
                severity = AlertSeverity.HIGH
                alert_type = AlertType.DEBRIS_FIELD
            elif cascade_mult >= 2:
                severity = AlertSeverity.MEDIUM
                alert_type = AlertType.DEBRIS_FIELD
            else:
                severity = AlertSeverity.INFO
                alert_type = AlertType.DEBRIS_FIELD

            title = f"{'⚠ THREAT ASSESSMENT' if alert_type == AlertType.THREAT_ASSESSMENT else 'DEBRIS FIELD'}: {deb_name}"
            summary = (
                f"Alt {altitude:.0f} km · {involvement_count} active conjunctions · "
                f"Cascade x{cascade_mult} · "
                f"Threat indicator: {indicator.value} · "
                f"Recommended: {recommended}"
            )

            alert = self._make_alert(
                alert_type=alert_type,
                severity=severity,
                generated_at=now,
                expires_at=now + timedelta(hours=24),
                title=title,
                summary=summary,
                affected_satellites=[deb_name],
                affected_operators=_infer_operators(["Debris"]),
                affected_shell_km=altitude,
                action_type=ActionType.REMEDIATION_TASK if cascade_mult >= 2 else ActionType.MONITOR,
                action_deadline_utc=intercept_close,
                remediation_spec=rem_spec,
                threat_assessment=threat,
            )
            new_alerts.append(alert)

        return new_alerts

    def _threat_notes(self, name: str, indicator: ThreatIndicator, asymmetry: float) -> str:
        if "FENGYUN" in name.upper():
            return ("China Fengyun-1C ASAT test (2007). Highest asymmetry score in catalog. "
                    "Fragment distribution inconsistent with natural fragmentation. "
                    "Geopolitical timing correlation HIGH. Route to USSPACECOM for review.")
        if "COSMOS" in name.upper():
            return ("Cosmos 2251 collision debris (2009). Natural collision event. "
                    "Fragment distribution consistent with hypervelocity impact physics.")
        if indicator == ThreatIndicator.NONE:
            return "No anomalous indicators. Consistent with natural fragmentation or collision."
        return f"Asymmetry score {asymmetry:.2f} — review recommended."

    # ── Remediation target ranking ────────────────────────────────────────────

    def get_remediation_targets(self) -> List[dict]:
        """Ranked list of debris objects for active removal vehicles."""
        targets = sorted(self._remediation_targets, key=lambda r: -r.priority_score)
        # Deduplicate by target name
        seen = set()
        out = []
        for t in targets:
            if t.target_name not in seen:
                seen.add(t.target_name)
                out.append(t.to_dict())
        return out

    # ── Alert management ──────────────────────────────────────────────────────

    def expire_stale(self):
        now = datetime.now(timezone.utc)
        for alert in self._alerts.values():
            if alert.status == AlertStatus.ACTIVE and alert.expires_at < now:
                alert.status = AlertStatus.EXPIRED

    def acknowledge(self, alert_id: str, operator: str = "") -> bool:
        if alert_id in self._alerts:
            self._alerts[alert_id].status = AlertStatus.ACKNOWLEDGED
            return True
        return False

    def resolve(self, alert_id: str) -> bool:
        if alert_id in self._alerts:
            self._alerts[alert_id].status = AlertStatus.RESOLVED
            return True
        return False

    def get_alerts(
        self,
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
        status: Optional[str] = None,
        satellite: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        self.expire_stale()
        results = list(self._alerts.values())

        if severity:
            results = [a for a in results if a.severity.value == severity.upper()]
        if alert_type:
            results = [a for a in results if a.alert_type.value == alert_type.upper()]
        if status:
            results = [a for a in results if a.status.value == status.upper()]
        if satellite:
            results = [a for a in results if satellite in a.affected_satellites]

        sev_order = {AlertSeverity.CRITICAL: 0, AlertSeverity.HIGH: 1,
                     AlertSeverity.MEDIUM: 2, AlertSeverity.INFO: 3}
        results.sort(key=lambda a: (sev_order[a.severity], a.generated_at), reverse=False)
        return [a.to_dict() for a in results[:limit]]

    def get_summary(self) -> dict:
        self.expire_stale()
        all_alerts = list(self._alerts.values())
        active = [a for a in all_alerts if a.status == AlertStatus.ACTIVE]
        counts = {s.value: 0 for s in AlertSeverity}
        for a in active:
            counts[a.severity.value] += 1
        threat_alerts = [a for a in active
                         if a.alert_type == AlertType.THREAT_ASSESSMENT
                         and a.threat_assessment
                         and a.threat_assessment.indicator in
                         (ThreatIndicator.MODERATE, ThreatIndicator.HIGH, ThreatIndicator.CONFIRMED)]
        return {
            "total_active": len(active),
            "total_all": len(all_alerts),
            "by_severity": counts,
            "threat_assessments": len(threat_alerts),
            "remediation_targets": len(self._remediation_targets),
        }

    def reset(self):
        self._alerts.clear()
        self._remediation_targets.clear()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _estimate_rendezvous_dv(target_altitude_km: float) -> float:
    """
    Rough Hohmann transfer estimate from 500km parking orbit to target.
    Good enough for planning; actual depends on phasing and launch window.
    """
    R_EARTH = 6371.0
    MU = 398600.4418
    r1 = R_EARTH + 500.0
    r2 = R_EARTH + target_altitude_km
    v1 = math.sqrt(MU / r1)
    v_transfer_peri = math.sqrt(2 * MU * r2 / (r1 * (r1 + r2)))
    v_transfer_apo  = math.sqrt(2 * MU * r1 / (r2 * (r1 + r2)))
    v2 = math.sqrt(MU / r2)
    dv1 = abs(v_transfer_peri - v1) * 1000
    dv2 = abs(v2 - v_transfer_apo) * 1000
    return round(dv1 + dv2, 1)


def _recommended_action(altitude_km: float, threat_score: float, cascade_mult: float) -> str:
    """Recommend removal method based on orbital mechanics and threat profile."""
    if altitude_km > 2000:
        return "MONITOR"            # MEO/GEO — deorbit is prohibitively expensive
    if altitude_km > 1200:
        return "REDIRECT"           # nudge to graveyard orbit
    if threat_score > 0.5:
        return "CAPTURE_PRIORITY"   # high threat → prioritize capture
    if cascade_mult >= 3:
        return "DEORBIT"            # high cascade driver → remove from shell
    if altitude_km < 600:
        return "DEORBIT"            # low enough for natural reentry assist
    return "CAPTURE"
