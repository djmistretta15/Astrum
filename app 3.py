"""
Astrum API Server
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from orbital_engine import OrbitalEngine, ConjunctionEvent
from maneuver_scheduler import ManeuverScheduler
from datetime import datetime, timezone
import threading

app = Flask(__name__)
CORS(app)

engine = OrbitalEngine(cache_dir="./tle_cache", live=True)
scheduler = ManeuverScheduler(target_miss_km=5.0, max_dv_per_sat_ms=3.0)
_lock = threading.Lock()
_last_plan = None


def _conj_to_dict(c: ConjunctionEvent) -> dict:
    return {
        "sat_a": c.sat_a,
        "sat_b": c.sat_b,
        "tca": c.tca.isoformat(),
        "tca_relative_hours": round((c.tca - datetime.now(timezone.utc)).total_seconds() / 3600, 2),
        "miss_distance_km": c.miss_distance,
        "closing_speed_kms": c.closing_speed,
        "probability": c.probability,
        "risk_level": c.risk_level,
        "delta_v_cost_ms": c.delta_v_cost,
        "cascade_risk": c.cascade_risk,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    with _lock:
        summary = engine.get_summary()
    return jsonify({"status": "operational", **summary})


@app.route("/api/satellites")
def satellites():
    with _lock:
        states = engine.propagate_all()
    return jsonify({"satellites": states, "count": len(states)})


@app.route("/api/conjunctions")
def conjunctions():
    lookahead = float(request.args.get("hours", 24))
    with _lock:
        conjs = engine.compute_conjunctions(lookahead_hours=lookahead)
    return jsonify({
        "conjunctions": [_conj_to_dict(c) for c in conjs],
        "total": len(conjs),
        "lookahead_hours": lookahead,
    })


@app.route("/api/risk-graph")
def risk_graph():
    with _lock:
        G = engine.risk_graph
        nodes = []
        for n, data in G.nodes(data=True):
            nodes.append({
                "id": n,
                "category": data.get("category", "Other"),
                "altitude": round(data.get("altitude", 0), 1),
                "risk_score": sum(G[n][nb]["weight"] for nb in G.neighbors(n)),
            })
        edges = []
        for a, b, data in G.edges(data=True):
            edges.append({
                "source": a, "target": b,
                "risk_level": data.get("risk_level", "LOW"),
                "miss_distance": data.get("miss_distance", 999),
                "weight": data.get("weight", 0.1),
                "delta_v": data.get("delta_v", 0),
            })
    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/cascade/<sat_name>")
def cascade(sat_name):
    name = sat_name.replace("_", " ").replace("%28", "(").replace("%29", ")")
    with _lock:
        result = engine.cascade_analysis(name)
    return jsonify(result)


@app.route("/api/maneuver-plan", methods=["GET", "POST"])
def maneuver_plan():
    """
    Compute or return the current maneuver plan.
    POST: recompute fresh plan
    GET: return cached plan (or compute if none)
    """
    global _last_plan
    force = request.method == "POST"

    with _lock:
        if force or _last_plan is None:
            # Ensure conjunctions are fresh
            conjs = engine.compute_conjunctions()
            states = engine.propagate_all()
            plan = scheduler.solve(conjs, states, engine.satellites)
            _last_plan = plan
        else:
            plan = _last_plan

    return jsonify(plan.to_dict())


@app.route("/api/maneuver-plan/satellite/<sat_name>")
def maneuver_for_satellite(sat_name):
    """Return maneuver recommendations for a specific satellite."""
    global _last_plan
    name = sat_name.replace("_", " ").replace("%28", "(").replace("%29", ")")

    with _lock:
        if _last_plan is None:
            conjs = engine.compute_conjunctions()
            states = engine.propagate_all()
            _last_plan = scheduler.solve(conjs, states, engine.satellites)

        recs = [r.to_dict() for r in _last_plan.recommendations if r.sat_name == name]
        cascade = engine.cascade_analysis(name)

    return jsonify({
        "satellite": name,
        "recommendations": recs,
        "cascade_analysis": cascade,
    })


@app.route("/api/data-status")
def data_status():
    """TLE cache health — useful for ops dashboard."""
    with _lock:
        cache_status = engine._loader.get_status()
        data_source = engine.data_source
    return jsonify({
        "data_source": data_source,
        "catalogs": cache_status,
    })


@app.route("/api/reload-tles", methods=["POST"])
def reload_tles():
    """Force-refresh all TLE data from Celestrak."""
    with _lock:
        count = engine.reload_tles(force=True)
        conjs = engine.compute_conjunctions()
        summary = engine.get_summary()
    return jsonify({"reloaded": True, "satellites": count, **summary})


@app.route("/api/refresh", methods=["POST"])
def refresh():
    global _last_plan
    with _lock:
        conjs = engine.compute_conjunctions()
        states = engine.propagate_all()
        _last_plan = scheduler.solve(conjs, states, engine.satellites)
        summary = engine.get_summary()
    return jsonify({"refreshed": True, "plan_computed": True, **summary})


if __name__ == "__main__":
    print("[Astrum] Running initial conjunction analysis...")
    engine.compute_conjunctions()
    print("[Astrum] Ready. Starting server at http://localhost:5000")
    app.run(host="0.0.0.0", port=5001, debug=False)
