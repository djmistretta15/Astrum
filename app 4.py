"""
Astrum API Server — v3
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from orbital_engine import OrbitalEngine
from maneuver_scheduler import ManeuverScheduler
from alert_engine import AlertEngine
from datetime import datetime, timezone
import threading

app = Flask(__name__)
CORS(app)

engine    = OrbitalEngine(cache_dir="./tle_cache", live=True)
scheduler = ManeuverScheduler(target_miss_km=5.0, max_dv_per_sat_ms=3.0)
alerter   = AlertEngine()
_lock     = threading.Lock()
_last_plan    = None
_last_refresh = None


def _conj_to_dict(c) -> dict:
    return {
        "sat_a": c.sat_a, "sat_b": c.sat_b,
        "tca": c.tca.isoformat(),
        "tca_relative_hours": round((c.tca - datetime.now(timezone.utc)).total_seconds() / 3600, 2),
        "miss_distance_km": c.miss_distance,
        "closing_speed_kms": c.closing_speed,
        "probability": c.probability,
        "risk_level": c.risk_level,
        "delta_v_cost_ms": c.delta_v_cost,
        "cascade_risk": c.cascade_risk,
    }


def _full_refresh():
    """Recompute conjunctions, plan, and all alerts atomically."""
    global _last_plan, _last_refresh
    conjs  = engine.compute_conjunctions()
    states = engine.propagate_all()
    plan   = scheduler.solve(conjs, states, engine.satellites)
    _last_plan = plan

    alerter.reset()
    alerter.generate_conjunction_alerts(conjs, plan, engine.satellites)
    alerter.generate_debris_field_alerts(conjs, engine.satellites)
    _last_refresh = datetime.now(timezone.utc)
    return conjs, plan


# ─── Core endpoints ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    with _lock:
        summary = engine.get_summary()
        alert_summary = alerter.get_summary()
    return jsonify({"status": "operational", **summary, "alerts": alert_summary})


@app.route("/api/satellites")
def satellites():
    with _lock:
        states = engine.propagate_all()
    return jsonify({"satellites": states, "count": len(states)})


@app.route("/api/conjunctions")
def conjunctions():
    hours = float(request.args.get("hours", 24))
    with _lock:
        conjs = engine.compute_conjunctions(lookahead_hours=hours)
    return jsonify({"conjunctions": [_conj_to_dict(c) for c in conjs], "total": len(conjs)})


@app.route("/api/risk-graph")
def risk_graph():
    with _lock:
        G = engine.risk_graph
        nodes = [{"id": n, **{k: round(v,1) if isinstance(v,float) else v
                  for k,v in d.items()},
                  "risk_score": round(sum(G[n][nb]["weight"] for nb in G.neighbors(n)), 3)}
                 for n, d in G.nodes(data=True)]
        edges = [{"source": a, "target": b, **d} for a, b, d in G.edges(data=True)]
    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/cascade/<sat_name>")
def cascade(sat_name):
    with _lock:
        result = engine.cascade_analysis(sat_name.replace("_", " "))
    return jsonify(result)


@app.route("/api/maneuver-plan", methods=["GET", "POST"])
def maneuver_plan():
    global _last_plan
    with _lock:
        if request.method == "POST" or _last_plan is None:
            _full_refresh()
        plan = _last_plan
    return jsonify(plan.to_dict())


@app.route("/api/maneuver-plan/satellite/<sat_name>")
def maneuver_for_satellite(sat_name):
    global _last_plan
    name = sat_name.replace("_", " ")
    with _lock:
        if _last_plan is None:
            _full_refresh()
        recs = [r.to_dict() for r in _last_plan.recommendations if r.sat_name == name]
        casc = engine.cascade_analysis(name)
    return jsonify({"satellite": name, "recommendations": recs, "cascade_analysis": casc})


# ─── Alert endpoints ──────────────────────────────────────────────────────────

@app.route("/api/alerts")
def alerts():
    """
    GET /api/alerts
    Query params: severity, type, status, satellite, limit
    """
    with _lock:
        if _last_refresh is None:
            _full_refresh()
        result = alerter.get_alerts(
            severity=request.args.get("severity"),
            alert_type=request.args.get("type"),
            status=request.args.get("status", "ACTIVE"),
            satellite=request.args.get("satellite"),
            limit=int(request.args.get("limit", 100)),
        )
        summary = alerter.get_summary()
    return jsonify({"alerts": result, "summary": summary})


@app.route("/api/alerts/<alert_id>/acknowledge", methods=["POST"])
def acknowledge_alert(alert_id):
    operator = request.json.get("operator", "unknown") if request.is_json else "unknown"
    with _lock:
        ok = alerter.acknowledge(alert_id, operator)
    return jsonify({"acknowledged": ok, "alert_id": alert_id})


@app.route("/api/alerts/<alert_id>/resolve", methods=["POST"])
def resolve_alert(alert_id):
    with _lock:
        ok = alerter.resolve(alert_id)
    return jsonify({"resolved": ok, "alert_id": alert_id})


# ─── Remediation endpoints ────────────────────────────────────────────────────

@app.route("/api/remediation-targets")
def remediation_targets():
    """
    Ranked debris objects for active removal vehicles.
    Sorted by priority score (cascade multiplier × threat score).
    """
    with _lock:
        if _last_refresh is None:
            _full_refresh()
        targets = alerter.get_remediation_targets()
    return jsonify({
        "targets": targets,
        "count": len(targets),
        "note": "Ranked by priority_score = cascade_multiplier × threat_composite × urgency"
    })


@app.route("/api/remediation-targets/<norad_id>")
def remediation_target_detail(norad_id):
    with _lock:
        targets = alerter.get_remediation_targets()
    match = next((t for t in targets if t["target_norad"] == norad_id), None)
    if not match:
        return jsonify({"error": "Target not found"}), 404
    return jsonify(match)


@app.route("/api/threat-summary")
def threat_summary():
    """
    Summary of threat-indicator debris fields.
    This is the intelligence product layer — routes to defense consumers.
    """
    with _lock:
        if _last_refresh is None:
            _full_refresh()
        all_alerts = alerter.get_alerts(alert_type="THREAT_ASSESSMENT", status="ACTIVE", limit=50)
        rem_targets = alerter.get_remediation_targets()
        threat_targets = [t for t in rem_targets
                         if t["threat_indicator"] not in ("NONE", "LOW")]
    return jsonify({
        "threat_alerts": all_alerts,
        "high_priority_targets": threat_targets,
        "classification": "UNCLASSIFIED // ASTRUM OPEN SOURCE FEED",
        "note": "Threat indicators are algorithmic scores, not confirmed intelligence. "
                "Route HIGH/CONFIRMED indicators to authorized operators for review."
    })


# ─── Data management ──────────────────────────────────────────────────────────

@app.route("/api/data-status")
def data_status():
    with _lock:
        return jsonify({
            "data_source": engine.data_source,
            "catalogs": engine._loader.get_status(),
            "last_refresh": _last_refresh.isoformat() if _last_refresh else None,
        })


@app.route("/api/reload-tles", methods=["POST"])
def reload_tles():
    with _lock:
        count = engine.reload_tles(force=True)
        _full_refresh()
        summary = engine.get_summary()
    return jsonify({"reloaded": True, "satellites": count, **summary})


@app.route("/api/refresh", methods=["POST"])
def refresh():
    with _lock:
        conjs, plan = _full_refresh()
        summary = engine.get_summary()
        alert_summary = alerter.get_summary()
    return jsonify({
        "refreshed": True,
        "conjunctions": len(conjs),
        "plan_recommendations": len(plan.recommendations),
        "alerts": alert_summary,
        **summary,
    })


if __name__ == "__main__":
    print("[Astrum] Running initial analysis...")
    with _lock:
        _full_refresh()
    print("[Astrum] Ready. http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
