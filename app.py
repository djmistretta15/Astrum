"""
Custodes API Server
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from orbital_engine import OrbitalEngine, ConjunctionEvent
from datetime import datetime, timezone
import threading
import time

app = Flask(__name__)
CORS(app)

engine = OrbitalEngine()
_lock = threading.Lock()

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
                "source": a,
                "target": b,
                "risk_level": data.get("risk_level", "LOW"),
                "miss_distance": data.get("miss_distance", 999),
                "weight": data.get("weight", 0.1),
                "delta_v": data.get("delta_v", 0),
            })
    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/cascade/<sat_name>")
def cascade(sat_name):
    sat_name_decoded = sat_name.replace("_", " ").replace("%28", "(").replace("%29", ")")
    with _lock:
        result = engine.cascade_analysis(sat_name_decoded)
    return jsonify(result)


@app.route("/api/refresh", methods=["POST"])
def refresh():
    with _lock:
        engine.compute_conjunctions()
        summary = engine.get_summary()
    return jsonify({"refreshed": True, **summary})


if __name__ == "__main__":
    # Pre-compute on startup
    print("[Custodes] Running initial conjunction analysis...")
    engine.compute_conjunctions()
    print("[Custodes] Ready. Starting server.")
    app.run(host="0.0.0.0", port=5000, debug=False)
