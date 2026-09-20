# NYC Rat Race API

FastAPI service (HTTP API + serves the built frontend) for the NYC Rat Race project.

## Run locally

Build the frontend first so the API has static files to serve:

```bash
cd ../frontend
npm install
npm run build
cd ../backend
```

Then create and activate a virtual environment, and install the project with
its development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,data,llm]'
```

Start the development server with:

```bash
uvicorn app.main:app --reload
```

The app is available at <http://127.0.0.1:8000> (the map + roaming rat).
Interactive API docs are at <http://127.0.0.1:8000/docs>.

## Frontend development

For hot-reload during frontend work, run the Vite dev server instead
(it proxies nothing; it just serves the map with HMR):

```bash
cd ../frontend
npm run dev
```

Open <http://127.0.0.1:5173>. Rebuild (`npm run build`) before restarting the
API so the served `dist/` matches.

If `frontend/dist/` does not exist, the API serves a JSON hint at `/` instead
of failing.

## Data source configuration

Without configuration, Grandpa uses a deterministic demo store. To make the
API read TLC Parquet files, set these variables before starting Uvicorn:

```bash
export RAT_RACE_TLC_GLOB='data/tlc_nyc/yellow/yellow_tripdata_*.parquet'
export RAT_RACE_TLC_ZONE_MAP='data/tlc_nyc/taxi_zone_game_map.csv'
export RAT_RACE_TLC_START_DATE='2019-01-01'
export RAT_RACE_TLC_END_DATE='2019-12-31'
export RAT_RACE_NOAA_GLOB='data/noaa_isd_nyc/*.csv'
export RAT_RACE_GDELT_GLOB='data/gdelt_nyc/events/gdelt_events_nyc_*.parquet'
```

Copy `.env.example` to `.env`, add your `GOOGLE_API_KEY` (free Gemini key from
<https://aistudio.google.com/apikey>), and load it into the shell before
starting the server:

```bash
cp .env.example .env
# edit .env and add the key
set -a; source .env; set +a
uvicorn app.main:app --reload
```

The API key is server-side only. Never send it to the frontend or commit it.
All adviser rats use this same client configuration; each rat supplies its own
personality prompt and analysis context. The model defaults to
`gemini-2.5-flash` (free-tier friendly) and is configurable via `GEMINI_MODEL`.

Without a Gemini key, each rat returns a deterministic template response with
the same contract (`narrative_source: "template"`), so local development does
not require network access.

The zone-map CSV must contain `LocationID` and `game_zone` columns, with
`game_zone` set to one of the nine Rat Race zone IDs. This mapping is kept
explicit because TLC's schema provides numeric location IDs, not the game's
macro-zone definitions.

The repository includes the official TLC lookup at
`data/tlc_nyc/taxi_zone_lookup.csv` and the generated gameplay mapping at
`data/tlc_nyc/taxi_zone_game_map.csv`. Rebuild it after replacing the official
lookup with:

```bash
python3 data-aquisition/build_zone_map.py \
  --lookup data/tlc_nyc/taxi_zone_lookup.csv \
  --output data/tlc_nyc/taxi_zone_game_map.csv
```

Stormy reads hourly NOAA observations and Gossip reads NYC-filtered GDELT
events from the configured paths. Because GDELT is daily, Gossip receives the
previous calendar day's aggregate; it never sees the current day's final news
total at the beginning of a simulation day.

## Test

```bash
pytest
```

## Adviser rats

The MVP exposes four specialized rats through one standardized response shape:

| Adviser | Specialty | Analytical tools |
| --- | --- | --- |
| Twitch | Mobility and momentum | recent demand, momentum, neighboring zones, flow propagation |
| Stormy | Weather | current weather, weather comparison, historical weather effect |
| Grandpa | Historical analogues | similar periods, historical outcomes |
| Gossip | Events and news | event activity, intensity, nearby events, historical event effect |

The Don is intentionally deferred until the four individual advisers have
enough history for a meaningful mixture-of-experts layer.

The frontend can discover the advisers with:

```bash
curl http://127.0.0.1:8000/api/advisers
```

Every adviser accepts the same request at
`POST /api/advisers/{adviser_id}`. The Grandpa-specific route remains as a
backward-compatible alias.

The demo adviser endpoint accepts the current state and returns historical
analogue evidence plus optional taxi moves:

```bash
curl -X POST http://127.0.0.1:8000/api/advisers/grandpa \
  -H 'Content-Type: application/json' \
  -d '{
    "timestamp": "2024-10-04T08:00:00",
    "idle_taxis_by_zone": {"upper_west": 10, "midtown": 2}
  }'
```

The same payload works for the other rats by changing the path:

```bash
curl -X POST http://127.0.0.1:8000/api/advisers/twitch \
  -H 'Content-Type: application/json' \
  -d '{
    "timestamp": "2024-10-04T08:00:00",
    "idle_taxis_by_zone": {"upper_west": 10, "midtown": 2}
  }'
```

Optional point-in-time signal fields are compact dictionaries. Stormy accepts
keys such as `rain_mm`, `temperature_c`, `wind_mps`, `visibility_m`, and
`snow_cm`. Gossip accepts a global `event_count`, `news_volume`, `num_sources`,
or `num_articles`, plus zone-specific keys such as
`event_count:midtown` or `event_intensity:midtown`.

When TLC is configured, Grandpa derives the current demand state from the
timestamp. You can still provide `demand_by_zone` when testing or when the
frontend already has the current state.

Every adviser uses the same response shape:

```json
{
  "schema_version": "1.0",
  "adviser_id": "grandpa",
  "adviser_name": "Grandpa",
  "specialty": "historical_analogues",
  "as_of": "2024-10-04T08:00:00",
  "horizon_hours": 3,
  "recommendation": "I compared similar historical periods...",
  "confidence": 0.82,
  "moves": [],
  "evidence": [],
  "forecast": {
    "horizon_hours": 3,
    "demand_by_zone": {},
    "predicted_revenue": 0
  },
  "matches_considered": 8,
  "data_source": "tlc-yellow",
  "tools_used": ["find_historical_analogues"]
}
```

Each rat runs its own numerical tools over compact point-in-time data, then
uses the shared Gemini client to interpret that verified analysis into
personality-specific natural language and a confidence value. Without
`GOOGLE_API_KEY`/`GEMINI_API_KEY`, each rat returns a deterministic template
response with the same contract.

To fetch one scenario month instead of the entire TLC archive:

```bash
python3 -m pip install -r data-aquisition/requirements.txt
python3 data-aquisition/tlc.py \
  --dataset yellow \
  --start-year 2019 --end-year 2019 \
  --start-month 10 --end-month 10 \
  --output data/tlc_nyc
```
