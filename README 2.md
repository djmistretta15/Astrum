# ASTRUM — Orbital Conjunction Scheduler

**The first multi-player conjunction avoidance scheduler for space.**

## Architecture
- `orbital_engine.py` — SGP4 propagation + conjunction detection + cascade analysis
- `app.py` — Flask API server
- `templates/index.html` — Command center dashboard

## Run
```bash
pip install -r requirements.txt
python3 app.py
# Open http://localhost:5000
```

## Live Data (Production)
Replace the `SEED_TLES` list in `orbital_engine.py` with a live Celestrak fetch:
```python
import requests
r = requests.get('https://celestrak.org/SOCRATES/query.php?...')
```
Celestrak catalogs: active.txt, stations.txt, debris.txt — all free, no auth required.

## API Endpoints
- `GET /api/status` — fleet risk summary
- `GET /api/satellites` — current ECI state vectors
- `GET /api/conjunctions?hours=24` — conjunction events
- `GET /api/risk-graph` — networkx graph as JSON
- `GET /api/cascade/<sat_name>` — Kessler cascade analysis
- `POST /api/refresh` — recompute all

## The Novel Insight
Collision avoidance is a multi-player coordination game, not a bilateral problem.
Current tools do pairwise checks. This engine models n-body cascade effects.
