# ASTRUM — Orbital Conjunction Scheduler

**The first multi-player conjunction avoidance scheduler for space.**

## What It Does
Current tools solve a pairwise problem: "you might hit X, consider a burn."  
Astrum solves the n-body coordination problem: globally optimal burn schedule
that minimizes total fleet delta-v while resolving all conjunctions, accounting
for cascade effects of each maneuver on every other object in the risk graph.

## Stack
```
orbital_engine.py       SGP4 propagation + conjunction detection + risk graph
tle_loader.py           Live Celestrak data fetcher with disk cache + TTL
tle_seed.py             Embedded fallback TLE data (25 satellites)
maneuver_scheduler.py   Multi-player burn optimizer (the core IP)
app.py                  Flask API server
templates/index.html    Orbital command center dashboard
```

## Run
```bash
pip install -r requirements.txt
python3 app.py
# → http://localhost:5001
```

## Live Data
Celestrak integration is built-in. On first run, TLEs are fetched automatically:
- Starlink, OneWeb, ISS, Planet Labs, GPS, Iridium
- Cosmos 2251 debris, Fengyun-1C debris, Iridium-33 debris

Cached to `./tle_cache/` with TTL per catalog (2-24 hours).
No API key required. Polite User-Agent included.

## API
| Endpoint | Description |
|---|---|
| `GET /api/status` | Fleet risk summary + cache status |
| `GET /api/satellites` | Current ECI state vectors |
| `GET /api/conjunctions?hours=24` | Conjunction events |
| `GET /api/risk-graph` | NetworkX graph as JSON |
| `GET /api/cascade/<sat>` | Kessler cascade analysis |
| `POST /api/maneuver-plan` | Compute optimal burn schedule |
| `GET /api/maneuver-plan/satellite/<sat>` | Burns for specific satellite |
| `POST /api/reload-tles` | Force-refresh all Celestrak data |
| `GET /api/data-status` | TLE cache health per catalog |

## The Novel IP
Collision avoidance is a multi-player coordination game, not bilateral.
The ManeuverScheduler runs cascade-aware greedy assignment across all
conjunctions simultaneously, respecting per-satellite delta-v budgets,
maneuverability constraints (debris can't burn), and second-order effects.
