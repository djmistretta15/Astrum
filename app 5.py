"""Astrum API Server v4 — all modules wired"""
from flask import Flask, jsonify, render_template, request, Response, stream_with_context
from flask_cors import CORS
from orbital_engine import OrbitalEngine
from maneuver_scheduler import ManeuverScheduler
from alert_engine import AlertEngine
from beacon_protocol import BeaconReplay
from operator_registry import OperatorRegistry
from event_log import EventLog, EventType, generate_cdm
from simulation_engine import SimulationEngine
from datetime import datetime, timezone
import threading, json, queue

app = Flask(__name__)
CORS(app)

engine    = OrbitalEngine(cache_dir="./tle_cache", live=True)
scheduler = ManeuverScheduler(target_miss_km=5.0, max_dv_per_sat_ms=3.0)
alerter   = AlertEngine()
registry  = OperatorRegistry("./registry.json")
elog      = EventLog("./event_log.jsonl")
sim       = SimulationEngine(engine, registry)

_lock, _last_plan, _last_conjs, _last_refresh = threading.Lock(), None, [], None
_sse_queues, _sse_lock = [], threading.Lock()


def _conj_to_dict(c):
    return {"sat_a":c.sat_a,"sat_b":c.sat_b,"tca":c.tca.isoformat(),
            "tca_relative_hours":round((c.tca-datetime.now(timezone.utc)).total_seconds()/3600,2),
            "miss_distance_km":c.miss_distance,"closing_speed_kms":c.closing_speed,
            "probability":c.probability,"risk_level":c.risk_level,
            "delta_v_cost_ms":c.delta_v_cost,"cascade_risk":c.cascade_risk}

def _broadcast(ev, data):
    msg = f"event: {ev}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead=[]; [q.put_nowait(msg) if not dead.append(q) else None
                  for q in _sse_queues if not _try_put(q,msg,dead)]
        [_sse_queues.remove(q) for q in dead]

def _try_put(q, msg, dead):
    try: q.put_nowait(msg); return True
    except: dead.append(q); return False

def _full_refresh():
    global _last_plan, _last_conjs, _last_refresh
    conjs  = engine.compute_conjunctions()
    states = engine.propagate_all()
    plan   = scheduler.solve(conjs, states, engine.satellites)
    _last_plan, _last_conjs = plan, conjs
    alerter.reset()
    alerter.generate_conjunction_alerts(conjs, plan, engine.satellites)
    alerter.generate_debris_field_alerts(conjs, engine.satellites)
    _last_refresh = datetime.now(timezone.utc)
    elog.log(EventType.TLE_REFRESHED,{"satellite_count":len(engine.satellites),"conjunction_count":len(conjs),"data_source":engine.data_source})
    for c in conjs[:5]:
        elog.log(EventType.CONJUNCTION_DETECTED,{"sat_a":c.sat_a,"sat_b":c.sat_b,"miss_km":c.miss_distance,"risk":c.risk_level,"tca":c.tca.isoformat()})
    _broadcast("refresh",{"timestamp":_last_refresh.isoformat(),"conjunctions":len(conjs),"alerts":alerter.get_summary()})
    return conjs, plan

# ── Pages ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index(): return render_template("index.html")
@app.route("/operator")
def operator_portal(): return render_template("operator_portal.html")

# ── Status ────────────────────────────────────────────────────────────────────
@app.route("/api/status")
def status():
    with _lock:
        return jsonify({"status":"operational","version":"4.0",**engine.get_summary(),
            "alerts":alerter.get_summary(),"registry":registry.get_summary(),
            "event_log":elog.get_summary(),"last_refresh":_last_refresh.isoformat() if _last_refresh else None})

# ── Core ──────────────────────────────────────────────────────────────────────
@app.route("/api/satellites")
def satellites():
    with _lock: states = engine.propagate_all()
    return jsonify({"satellites":states,"count":len(states)})

@app.route("/api/conjunctions")
def conjunctions():
    hours = float(request.args.get("hours",24))
    with _lock: conjs = engine.compute_conjunctions(lookahead_hours=hours)
    return jsonify({"conjunctions":[_conj_to_dict(c) for c in conjs],"total":len(conjs)})

@app.route("/api/risk-graph")
def risk_graph():
    with _lock:
        G=engine.risk_graph
        nodes=[{"id":n,**{k:round(v,1) if isinstance(v,float) else v for k,v in d.items()},
                "risk_score":round(sum(G[n][nb]["weight"] for nb in G.neighbors(n)),3)} for n,d in G.nodes(data=True)]
        edges=[{"source":a,"target":b,**d} for a,b,d in G.edges(data=True)]
    return jsonify({"nodes":nodes,"edges":edges})

@app.route("/api/cascade/<path:sat_name>")
def cascade(sat_name):
    with _lock: result=engine.cascade_analysis(sat_name.replace("_"," "))
    return jsonify(result)

# ── Maneuver ──────────────────────────────────────────────────────────────────
@app.route("/api/maneuver-plan", methods=["GET","POST"])
def maneuver_plan():
    global _last_plan
    with _lock:
        if request.method=="POST" or _last_plan is None: _full_refresh()
    return jsonify(_last_plan.to_dict())

@app.route("/api/maneuver-plan/satellite/<path:sat_name>")
def maneuver_for_satellite(sat_name):
    global _last_plan
    name=sat_name.replace("_"," ")
    with _lock:
        if _last_plan is None: _full_refresh()
        recs=[r.to_dict() for r in _last_plan.recommendations if r.sat_name==name]
        casc=engine.cascade_analysis(name)
    return jsonify({"satellite":name,"recommendations":recs,"cascade_analysis":casc})

# ── Alerts ────────────────────────────────────────────────────────────────────
@app.route("/api/alerts")
def alerts():
    with _lock:
        if _last_refresh is None: _full_refresh()
        result=alerter.get_alerts(severity=request.args.get("severity"),alert_type=request.args.get("type"),
            status=request.args.get("status","ACTIVE"),satellite=request.args.get("satellite"),limit=int(request.args.get("limit",100)))
        summary=alerter.get_summary()
    return jsonify({"alerts":result,"summary":summary})

@app.route("/api/alerts/<alert_id>/acknowledge",methods=["POST"])
def acknowledge_alert(alert_id):
    op=request.json.get("operator","unknown") if request.is_json else "unknown"
    with _lock:
        ok=alerter.acknowledge(alert_id,op)
        if ok: elog.log(EventType.ALERT_ACKNOWLEDGED,{"alert_id":alert_id,"operator":op})
    return jsonify({"acknowledged":ok,"alert_id":alert_id})

@app.route("/api/alerts/<alert_id>/resolve",methods=["POST"])
def resolve_alert(alert_id):
    with _lock:
        ok=alerter.resolve(alert_id)
        if ok: elog.log(EventType.ALERT_RESOLVED,{"alert_id":alert_id})
    return jsonify({"resolved":ok,"alert_id":alert_id})

# ── Remediation ───────────────────────────────────────────────────────────────
@app.route("/api/remediation-targets")
def remediation_targets():
    with _lock:
        if _last_refresh is None: _full_refresh()
        targets=alerter.get_remediation_targets()
    return jsonify({"targets":targets,"count":len(targets)})

@app.route("/api/remediation-targets/<norad_id>")
def remediation_target_detail(norad_id):
    with _lock: targets=alerter.get_remediation_targets()
    match=next((t for t in targets if t["target_norad"]==norad_id),None)
    return jsonify(match) if match else (jsonify({"error":"Not found"}),404)

# ── Threat Feed ───────────────────────────────────────────────────────────────
@app.route("/api/threat-feed")
def threat_feed():
    with _lock:
        if _last_refresh is None: _full_refresh()
        threat_alerts=alerter.get_alerts(alert_type="THREAT_ASSESSMENT",status="ACTIVE",limit=50)
        rem=alerter.get_remediation_targets()
        head=elog.get_summary()["head_hash"]
    return jsonify({"feed_version":"1.0","timestamp":datetime.now(timezone.utc).isoformat(),
        "classification":"UNCLASSIFIED // ASTRUM OPEN SOURCE INTELLIGENCE FEED",
        "originator":"ASTRUM ORBITAL SCHEDULER / astrum.space","event_log_head":head,
        "threat_assessments":threat_alerts,"high_priority_targets":[t for t in rem if t["threat_indicator"] not in("NONE","LOW")],
        "disclaimer":"Threat indicators are algorithmic scores. Route HIGH/CONFIRMED to authorized analysts."})

# ── Simulation ────────────────────────────────────────────────────────────────
@app.route("/api/simulate/scenarios")
def sim_scenarios(): return jsonify({"scenarios":sim.get_available_scenarios()})

@app.route("/api/simulate/run",methods=["POST"])
def sim_run():
    data=request.json or {}
    scenario_name=data.get("scenario"); custom_params=data.get("custom")
    if not scenario_name and not custom_params:
        return jsonify({"error":"Provide 'scenario' or 'custom' params"}),400
    with _lock:
        result=sim.run_scenario(scenario_name=scenario_name,custom_params=custom_params)
        elog.log(EventType.SIMULATION_RUN,{"scenario":scenario_name or "custom",
            "fragment_count":result.event.fragment_count,"kessler_score":result.kessler_risk_score,
            "affected_satellites":result.total_affected_satellites})
    _broadcast("simulation",{"event_id":result.event.event_id,"scenario":scenario_name or "custom",
        "kessler_score":result.kessler_risk_score,"affected":result.total_affected_satellites,"summary":result.summary})
    return jsonify(result.to_dict())

@app.route("/api/simulate/result/<event_id>")
def sim_result(event_id):
    with _lock: result=sim.get_last_result(event_id)
    return jsonify(result) if result else (jsonify({"error":"Not found"}),404)

# ── CDM Export ────────────────────────────────────────────────────────────────
@app.route("/api/cdm/<int:conj_index>")
def cdm_export(conj_index):
    with _lock:
        if not _last_conjs: _full_refresh()
        conjs=_last_conjs
    if conj_index>=len(conjs): return jsonify({"error":"Index out of range"}),404
    cdm=generate_cdm(conjs[conj_index],engine.satellites,registry,conj_index)
    return Response(cdm,mimetype="text/plain",headers={"Content-Disposition":f"attachment; filename=astrum_cdm_{conj_index:03d}.txt"})

@app.route("/api/cdm/all")
def cdm_all():
    with _lock:
        if not _last_conjs: _full_refresh()
        docs=[generate_cdm(c,engine.satellites,registry,i) for i,c in enumerate(_last_conjs)]
    return Response("\n---\n".join(docs),mimetype="text/plain",headers={"Content-Disposition":"attachment; filename=astrum_cdm_all.txt"})

# ── Beacons ───────────────────────────────────────────────────────────────────
@app.route("/api/beacons")
def beacons():
    with _lock:
        if _last_refresh is None: _full_refresh()
        br=BeaconReplay(engine,_last_conjs); packets=br.generate_fleet_beacons()
    return jsonify({"beacons":packets,"count":len(packets),"protocol_version":"1.0","packet_size_bytes":123})

# ── Operators ─────────────────────────────────────────────────────────────────
@app.route("/api/operators")
def operators_list(): return jsonify({"operators":registry.get_all_operators(),"summary":registry.get_summary()})

@app.route("/api/operators/register",methods=["POST"])
def operators_register():
    data=request.json or {}
    missing=[f for f in["name","org_type","tier","contact_email"] if f not in data]
    if missing: return jsonify({"error":f"Missing: {missing}"}),400
    result=registry.register_operator(data["name"],data["org_type"],data["tier"],data["contact_email"],data.get("satellite_norads",[]))
    elog.log(EventType.OPERATOR_REGISTERED,{"name":data["name"],"tier":data["tier"]})
    return jsonify(result),201

@app.route("/api/operators/<op_id>")
def operator_detail(op_id):
    op=registry.get_operator(op_id)
    if not op: return jsonify({"error":"Not found"}),404
    return jsonify({**op.to_dict(include_sensitive=True),"permissions":registry.get_permissions(op_id)})

@app.route("/api/operators/<op_id>/satellites")
def operator_satellites(op_id):
    op=registry.get_operator(op_id)
    if not op: return jsonify({"error":"Not found"}),404
    norads=registry.get_operator_satellites(op_id)
    with _lock: states=engine.propagate_all()
    norad_to_name={sat.norad_id:name for name,sat in engine.satellites.items()}
    result=[{"norad_id":n,"name":norad_to_name.get(n,f"NORAD-{n}"),**states.get(norad_to_name.get(n,""),{})} for n in norads]
    return jsonify({"operator":op.name,"satellites":result,"count":len(result)})

@app.route("/api/operators/<op_id>/alerts")
def operator_alerts(op_id):
    op=registry.get_operator(op_id)
    if not op: return jsonify({"error":"Not found"}),404
    norads=set(registry.get_operator_satellites(op_id))
    norad_to_name={sat.norad_id:name for name,sat in engine.satellites.items()}
    owned=[norad_to_name[n] for n in norads if n in norad_to_name]
    with _lock:
        if _last_refresh is None: _full_refresh()
        seen=set(); deduped=[]
        for sat_name in owned:
            for a in alerter.get_alerts(satellite=sat_name,limit=20):
                if a["alert_id"] not in seen: seen.add(a["alert_id"]); deduped.append(a)
    return jsonify({"operator":op.name,"alerts":deduped,"count":len(deduped)})

# ── Events ────────────────────────────────────────────────────────────────────
@app.route("/api/events")
def events(): return jsonify({"events":elog.get_entries(event_type=request.args.get("type"),limit=int(request.args.get("limit",50))),"summary":elog.get_summary()})

@app.route("/api/events/chain-verify")
def chain_verify(): return jsonify(elog.verify_chain())

# ── SSE Stream ────────────────────────────────────────────────────────────────
@app.route("/api/stream")
def sse_stream():
    q=queue.Queue(maxsize=100)
    with _sse_lock: _sse_queues.append(q)
    def generate():
        yield f"event: connected\ndata: {json.dumps({'ts':datetime.now(timezone.utc).isoformat()})}\n\n"
        try:
            while True:
                try: yield q.get(timeout=30)
                except queue.Empty: yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_queues: _sse_queues.remove(q)
    return Response(stream_with_context(generate()),mimetype="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── Data ──────────────────────────────────────────────────────────────────────
@app.route("/api/data-status")
def data_status():
    with _lock: return jsonify({"data_source":engine.data_source,"catalogs":engine._loader.get_status(),"last_refresh":_last_refresh.isoformat() if _last_refresh else None})

@app.route("/api/reload-tles",methods=["POST"])
def reload_tles():
    with _lock: count=engine.reload_tles(force=True); _full_refresh(); summary=engine.get_summary()
    return jsonify({"reloaded":True,"satellites":count,**summary})

@app.route("/api/refresh",methods=["POST"])
def refresh():
    with _lock: conjs,plan=_full_refresh(); summary=engine.get_summary(); alert_s=alerter.get_summary()
    return jsonify({"refreshed":True,"conjunctions":len(conjs),"plan_recommendations":len(plan.recommendations),"alerts":alert_s,**summary})

if __name__=="__main__":
    print("[Astrum v4] Initializing...")
    with _lock: _full_refresh()
    print(f"[Astrum v4] {len(engine.satellites)} sats | {len(_last_conjs)} conjunctions")
    print(f"[Astrum v4] {alerter.get_summary()}")
    print("[Astrum v4] Ready → http://localhost:5001")
    app.run(host="0.0.0.0",port=5001,debug=False,threaded=True)
