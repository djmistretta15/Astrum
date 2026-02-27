# ASTRUM — Orbital Conjunction Avoidance Scheduler
### v4.0 · Cooperative Space Situational Awareness Platform

---

## What It Is

Astrum is a **multi-player orbital conjunction scheduler** — the missing coordination layer for the commercial space economy. It's the air traffic control system Earth's orbital shells have never had.

When Starlink and OneWeb both detect a close approach, neither operator has visibility into what the other is planning. Astrum solves this: a shared risk graph, coordinated maneuver plans, and real-time alerts that reach every affected operator simultaneously.

**Business model:** Three-entity structure.
- **Astrum Foundation** — open beacon protocol standard, public risk data
- **Astrum Intelligence** — operator subscriptions (tiered API access, CDM export, scoped dashboards)
- **Astrum Sentinel** — defense/government licensing (threat feed, signed intelligence exports)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  nginx  (rate-limiting, SSE passthrough, TLS)           │
├─────────────────────────────────────────────────────────┤
│  Flask API — 25 endpoints                               │
├──────────────┬──────────────┬──────────────┬────────────┤
│ orbital_     │ maneuver_    │ alert_       │ simulation │
│ engine.py    │ scheduler.py │ engine.py    │ _engine.py │
│ SGP4+cascade │ RIC frame ΔV │ lifecycle +  │ NASA SBM   │
│ 9 Celestrak  │ optimizer    │ threat attrib│ 4 scenarios│
├──────────────┴──────────────┴──────────────┴────────────┤
│ beacon_protocol.py  │  operator_registry.py             │
│ Ed25519 123-byte    │  Multi-tier + webhook routing     │
├─────────────────────┴───────────────────────────────────┤
│  event_log.py — SHA-256 hash-chained ledger + CDM      │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5001           Global dashboard
# → http://localhost:5001/operator  Operator portal

# Docker
docker-compose up --build
```

---

## API Reference

### Core
```
GET  /api/status
GET  /api/satellites
GET  /api/conjunctions?hours=24
GET  /api/risk-graph
GET  /api/cascade/<sat_name>
```

### Maneuver Planning
```
GET/POST /api/maneuver-plan
GET      /api/maneuver-plan/satellite/<name>
```

### Alerts
```
GET  /api/alerts?severity=CRITICAL&status=ACTIVE
POST /api/alerts/<id>/acknowledge   body: {"operator": "..."}
POST /api/alerts/<id>/resolve
GET  /api/remediation-targets
```

### Simulation
```
GET  /api/simulate/scenarios
POST /api/simulate/run           body: {"scenario": "fengyun2"}
GET  /api/simulate/result/<id>

Scenarios: fengyun2 | cosmos_repeat | starlink_killer | battery
```

### Export
```
GET  /api/cdm/<index>    CCSDS CDM KVN for conjunction N
GET  /api/cdm/all        Full CDM bundle
```

### Intelligence
```
GET  /api/threat-feed    Signed threat feed (Sentinel product)
GET  /api/beacons        Fleet beacon packets
```

### Operators
```
GET  /api/operators
POST /api/operators/register
GET  /api/operators/<id>
GET  /api/operators/<id>/satellites
GET  /api/operators/<id>/alerts
```

### Events
```
GET  /api/events?type=CONJUNCTION_DETECTED&limit=50
GET  /api/events/chain-verify
```

### Live Stream (SSE)
```
GET  /api/stream

const es = new EventSource('/api/stream');
es.addEventListener('refresh', e => ...);
es.addEventListener('simulation', e => ...);
```

---

## Operator Tiers

| Tier | Rate | Features |
|------|------|---------|
| FREE | 10/min | Public dashboard, 24h delay |
| STANDARD | 60/min | Real-time alerts, CDM, webhooks, scoped view |
| PREMIUM | 300/min | + Maneuver recommendations, cascade analysis |
| DEFENSE | 1000/min | + Threat feed, signed exports, sees all satellites |

```bash
curl -X POST /api/operators/register -d '{
  "name": "NewSat-Ops", "org_type": "COMMERCIAL",
  "tier": "STANDARD", "contact_email": "ops@newsat.com",
  "satellite_norads": ["47001", "47002"]
}'
# Returns API key once — store securely
```

---

## Beacon Protocol

Ed25519 signed heartbeat. 123 bytes base packet, 134 bytes with conjunction sub-packet.

```
[4]  magic 0x41535452  [1] version  [1] type
[5]  norad_id           [8] timestamp (µs)
[3]  position_r (ECI)  [2] altitude_km  [2] inclination
[1]  flags              [32] pubkey (Ed25519)  [64] signature
```

Private keys never leave the flight computer. Stateless verification.

---

## Simulation Engine

NASA Standard Breakup Model (simplified). Fragment count:
```
N(>10cm) ≈ 6 × (reduced_mass)^0.75 × f(kinetic_energy)
```

Kessler Risk Score 0-100: altitude factor (800-1100km most dangerous) × population density × fragment energy × scenario type.

Fengyun-2 at 850km scores **81/100** — INTERVENTION REQUIRED.

---

## Defense Licensing

`/api/threat-feed` returns: threat assessments (cloud asymmetry, mass distribution anomaly, timing correlation), high-priority targets, event log head hash for chain-of-custody.

License to Leidos / Northrop / L3Harris for C2 integration. Annual fee per classification level.

---

## Files

```
app.py                  API — 25 endpoints, SSE, all modules wired
orbital_engine.py       SGP4, conjunction detection, cascade graph
maneuver_scheduler.py   Multi-player ΔV optimizer
alert_engine.py         Alert lifecycle, threat attribution, remediation
beacon_protocol.py      Ed25519 signed 123-byte packet spec
operator_registry.py    Multi-tier accounts, webhook routing
event_log.py            Hash-chained ledger + CCSDS CDM export
simulation_engine.py    NASA SBM + cascade time-series projector
tle_loader.py           Celestrak live fetch, 9 catalogs, seed fallback
templates/
  index.html            Global command center (D3 visualization)
  operator_portal.html  Scoped operator mission control
Dockerfile, docker-compose.yml, nginx.conf
```

---

## Demo Script

1. Open `/` → live conjunction graph, alerts, threat scores
2. Click **Simulate → Fengyun-2 ASAT Strike**
3. 153 fragments, 7 sats threatened, Kessler: 81/100
4. Operator alert sequence fires — SpaceX CRITICAL at T+0
5. Export CDM → drops into any operator's existing conjunction tooling (zero integration)
6. Switch to `/operator` → scoped view, just their fleet, their alerts
7. Show `/api/threat-feed` → pipe directly into Leidos/Northrop C2

Nobody else has a live fragmentation simulator that generates operator-specific cascade alerts in real time. This is what closes deals.

---

*Astrum is built for a world where orbital shells are shared infrastructure.*
