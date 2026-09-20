# Rat Race
## Consolidated Product and Engineering Specification

Rat Race is an interactive historical simulation and decision lab. The player
runs Rat Cab Co., a small NYC taxi company, while adviser rats analyze noisy
signals and recommend where the fleet should move.

This is the canonical merged specification. [rat-game.md](rat-game.md) remains
the original concept memo; this document resolves its conflicts with the newer
implementation plan.

## 1. Resolved differences

| Topic | Original concept | Merged MVP decision |
| --- | --- | --- |
| Game window | Start at 17:00 and reveal 30 minutes | Replay 08:00–20:00 for 3 days |
| Decision cadence | 30 minutes | Four 3-hour turns per day, 12 turns total |
| Fleet | 100 taxis | DEFAULT_FLEET_SIZE = 130, configurable per scenario |
| Map | Chelsea, SoHo, UES examples | Nine fixed macro-zones |
| Data | Start with TLC aggregation | Real TLC replay with NOAA/GDELT signals; synthetic mode remains for tests |
| Forecast | Next 30 minutes | Next 3 hours; 15-minute features and 30-minute diagnostics can come later |
| Advisers | LLM/tool-using agents | Rule-based mock advisers first, with typed analysis functions for future LLMs |
| Competition | Each adviser has a fleet | Adviser recommendations in MVP; parallel adviser fleets later |
| Score | Trip score and regret | Net earnings for the player; regret and trip metrics for research |
| Map | Mapbox/Deck.gl suggested | Custom SVG/React map first |
| The Don | Mixture-of-experts meta-adviser | Future stretch goal after individual advisers work |

The fixed MVP loop is:

~~~
3 operating days × 4 turns per day → 12 player decisions → 3 historical hours per decision
~~~

The real-data MVP starts at 2019-10-18 08:00 and replays October 18–20, 2019.
The earlier 17:00 idea remains a useful benchmark interval inside that window.

## 2. Product and modeling assumptions

The future is hidden at each decision point. Historical demand defines the
environment; a forecast predicts it; advisers interpret signals; the player
allocates taxis; and the simulator determines what Rat Cab captures.

~~~
Historical data → defines reality
Forecast model  → predicts reality
Advisers       → interpret different signals
Player         → allocates Rat Cab taxis
Simulator      → determines what Rat Cab captures
~~~

Rat Cab is small relative to the city. Player actions change only Rat Cab's
fleet, captured trips, costs, and future locations. They do not change citywide
historical demand.

The default local game uses the real TLC replay provider with NOAA weather and
previous-day GDELT aggregates. A transparent recent-demand baseline supplies
the player-facing forecast until the trained demand model and fictional-event
generator are ready. Synthetic data remains available for fast tests and
fallback development.

## 3. Clock and turns

Each game covers three consecutive simulation dates, but only the 08:00–20:00
operating window is played. Each day has four 3-hour turns:

~~~
Day 1: Turn  1   08:00 → 11:00
       Turn  2   11:00 → 14:00
       Turn  3   14:00 → 17:00
       Turn  4   17:00 → 20:00

Day 2: Turn  5   08:00 → 11:00
       Turn  6   11:00 → 14:00
       Turn  7   14:00 → 17:00
       Turn  8   17:00 → 20:00

Day 3: Turn  9   08:00 → 11:00
       Turn 10   11:00 → 14:00
       Turn 11   14:00 → 17:00
       Turn 12   17:00 → 20:00
~~~

There are exactly 12 player decisions. The overnight periods from 20:00 to
08:00 are closed hours: no player turns are spent on them and no trips are
simulated during them. After the final turn of a day, advance the calendar to
08:00 on the next date before the next decision.

The player repositions taxis before each 3-hour block. Inside a block, process
pickup events, dropoffs, and repositioning arrivals in chronological order. A
taxi that arrives after 20 historical minutes becomes available after 20
minutes, not at the next turn. Replay should take about 3–6 real seconds; do
not use a wall-clock countdown.

## 4. Nine-zone NYC map

Use exactly these zones and stable IDs:

| Zone | ID |
| --- | --- |
| Harlem | harlem |
| Upper West | upper_west |
| Upper East | upper_east |
| Midtown | midtown |
| Downtown | downtown |
| North Brooklyn | north_brooklyn |
| South Brooklyn | south_brooklyn |
| Queens West | queens_west |
| Airports | airports |

Examples from the original concept map as follows:

~~~
Chelsea → Midtown
SoHo    → Downtown
UES     → Upper East
~~~

Use a polished custom SVG/React map. Each zone shows name, idle/busy/
repositioning taxi counts, recent demand, forecast demand, selection, and
optional demand shading. Defer Mapbox and Deck.gl.

## 5. Fleet and player action

Use:

~~~
DEFAULT_FLEET_SIZE = 130
~~~

Fleet size is scenario-configurable and must not be hardcoded throughout the
application. All strategies start with the same fleet and distribution. A
reasonable synthetic distribution is:

~~~
Harlem              7
Upper West         10
Upper East         12
Midtown            30
Downtown           18
North Brooklyn     12
South Brooklyn      6
Queens West        12
Airports           23
~~~

Each taxi has:

~~~
id
status
current_zone
available_at
destination_zone
current_trip_id
~~~

Statuses are idle, busy, and repositioning. Only idle taxis can be moved.
Repositioning taxis cannot accept passenger trips.

The player-facing decision is a target allocation, not a move queue. In each
round the player:

1. enters one demand prediction for each of the nine zones; and
2. enters the number of idle taxis to place in each zone.

The allocation must use every currently available idle taxi. The backend
derives the minimal validated repositioning moves from that target. The
low-level move representation remains useful for simulation and tests:

~~~
FROM zone
TO zone
NUMBER of taxis
~~~

Validate enough idle taxis, a positive count, different zones, and no taxi
being committed twice. The player does not edit these derived moves directly.

Do not add pricing, hiring, driver schedules, marketing, upgrades, surge
pricing, passenger selection, route selection, or fuel systems.

Use a configurable 9×9 mock travel-time matrix, for example:

~~~
Midtown → Downtown        20 minutes
Midtown → Upper East      15 minutes
Midtown → Airports        45 minutes
Downtown → North Brooklyn 25 minutes
~~~

Cost is:

~~~
reposition_cost = travel_minutes * COST_PER_REPOSITION_MINUTE
~~~

## 6. Synthetic trips and simulation

Generate deterministic synthetic trips for the full day with a fixed seed.
Demand should vary across overnight, morning commute, midday, afternoon,
evening rush, dinner/nightlife, and airport regimes.

Normalized trips contain:

~~~
trip_id
pickup_time
dropoff_time
pickup_zone
dropoff_zone
fare_amount
trip_distance
~~~

For each opportunity:

1. Find an idle Rat Cab taxi in the pickup zone.
2. Assign it, mark it busy, and add the fare if one exists.
3. At dropoff, make it idle in the historical destination zone.
4. Otherwise increment trips_missed.

Choose any available taxi in a zone for the MVP. No within-zone dispatch
optimization is needed.

### Point-in-time rule

Player-facing state, forecasts, and adviser inputs may only use:

~~~
source timestamp <= current_simulation_time
~~~

The next block's outcomes stay hidden until the player commits and the
simulator advances. This prevents lookahead bias in the future historical
replay.

## 7. Schema-informed data architecture

Synthetic providers should implement small interfaces:

~~~
trips_for_interval(start, end)
zone_features_at(timestamp)
weather_at(timestamp)
events_at(timestamp)
~~~

### Weather cadence

Use hourly weather observations for the real-data adapter. NOAA's Global
Hourly / Integrated Surface Database provides hourly and synoptic observations,
which can be aligned to the game clock and aggregated over each 3-hour turn.
For a turn, use features such as mean temperature, precipitation total, mean
or maximum wind, minimum visibility, and a rain or severe-weather indicator.

The interface may still expose a single weather state at a timestamp; the
preprocessing layer should retain the hourly observations so that a 3-hour
forecast does not collapse the whole day into one value.

### GDELT cadence and leakage rule

The current GDELT input is treated as daily-granularity data. A daily aggregate
must not be interpreted as if the final 20:00 total were known at 08:00. Use one
of these safe approaches:

- use prior-day GDELT features as an input to the next day's game;
- use a day-level signal explicitly declared available at that day's 08:00;
- if raw event timestamps are available, aggregate only records at or before
  the current simulation time.

The data adapter must record which approach produced each event feature. The
current real-data path uses the previous calendar day's GDELT aggregate and
records that source as lagged daily data.

The first real-data adapter should use NYC TLC yellow taxi data. The canonical
yellow fields described in data/tlc_nyc/SCHEMA.md are:

~~~
tpep_pickup_datetime
tpep_dropoff_datetime
PULocationID
DOLocationID
fare_amount
trip_distance
total_amount
~~~

Normalize into:

~~~
trip_id
pickup_time
dropoff_time
pickup_zone_id
dropoff_zone_id
pickup_game_zone
dropoff_game_zone
fare_amount
trip_distance
~~~

TLC datetimes are timezone-naive New York wall time. Map TLC location IDs to
the nine macro-zones through the taxi-zone lookup table.

The adapter must explicitly cast drifting physical types, handle
airport_fee versus Airport_fee, normalize null-versus-zero fees, filter
out-of-range timestamps, use service-specific field names, and read Parquet
month-by-month or in bounded batches. Start with yellow taxis only: green, FHV,
and HVFHV have different schemas, and FHV has no fare fields.

Aggregate features at:

~~~
gameplay zone × 15-minute interval
~~~

Suggested fields include timestamp, zone_id, pickup/dropoff counts, incoming
and outgoing flow, fare total, lags at 15m/30m/1h/2h, rolling means, weather,
and GDELT event counts, mentions, sources, tone, and intensity.

Keep individual trips for replay. The planned pipeline is:

~~~
TLC / NOAA / GDELT raw data
        ↓
Polars normalization and preprocessing
        ↓
Parquet trip and feature files
        ↓
DuckDB analytical queries
        ↓
FastAPI simulation and advisers
~~~

## 8. Forecast service

Eventually use LightGBM to predict opportunities in the next three hours from
point-in-time features such as zone, hour, weekday, month, recent demand,
rolling demand, momentum, neighboring-zone activity, weather, and event
intensity.

Targets:

~~~
next_3h_trip_count
next_3h_total_fare
~~~

Do not train it in the first implementation. Define ForecastService and
MockForecastService returning zone, predicted_trip_count, and
predicted_revenue. Forecasts should be plausible but imperfect. Quantile
forecasts and a 30-minute research target are later extensions.

## 9. Adviser rats

The MVP has four deterministic, rule-based advisers. Each returns name,
specialty, short recommendation, confidence, recommended moves, and evidence.
They receive compact analytical results rather than raw rows.

### Twitch — mobility and momentum

Uses recent trip counts, acceleration, zone flows, and neighboring activity.

~~~
get_recent_demand(zone)
calculate_momentum(zone)
compare_neighboring_zones(zone)
flow_propagation(zone)
~~~

### Stormy — weather

Uses rain, temperature, visibility, wind, and historical demand in similar
conditions. NOAA is the planned source.

~~~
get_weather()
get_historical_weather_effect(zone)
compare_weather_condition()
~~~

### Grandpa — historical analogues

Searches for similar time-of-day, weekday, season, demand, weather, and
geographic states. The implemented first agent reads compact TLC-derived
3-hour aggregates, calls historical-analogue and outcome tools, and returns
natural-language advice through the shared adviser response schema. Its
narrative uses the shared OpenAI client when OPENAI_API_KEY is configured, with
a deterministic local fallback. Other rats can reuse the same client while
supplying their own personality prompt and analysis context.

~~~
find_similar_periods()
get_outcomes_for_similar_periods()
~~~

### Gossip — events and news

Uses synthetic events now and NYC-filtered GDELT later: event counts, news
volume, source count, nearby events, themes, and unusual intensity.

~~~
get_event_activity(zone)
get_event_intensity(zone)
get_nearby_events(zone)
~~~

Later, each adviser can become:

~~~
LLM + system prompt + allowed analysis functions + current game state
~~~

Numerical analysis remains in Python/Polars/DuckDB. The LLM only interprets
compact results and explains the recommendation.

### The Don

The Don is a later mixture-of-experts adviser. It can weight advisers based on
rain severity, time of day, volatility, event intensity, analogue quality, and
recent performance:

~~~
A(t) = Σ w_i(t) A_i(t)
~~~

Do not include The Don in the initial four-adviser MVP.

## 10. Score and evaluation

Player-facing score:

~~~
gross_fare_revenue
repositioning_cost
net_earnings = gross_fare_revenue - repositioning_cost
~~~

Also track trips_captured, trips_missed, revenue per taxi, capture rate, idle
time, and repositioning efficiency. A secondary research score may be:

~~~
trip_score = trips_captured
             - 0.2 * repositioned_taxis
             - 0.5 * trips_missed
~~~

After actual demand is revealed, calculate perfect-foresight performance and:

~~~
regret = score_perfect_foresight - score_player
~~~

Lower regret is better. Later compare forecast MAE/RMSE, quantile loss,
individual adviser regret, equal-weight advisers, The Don, human decisions,
and human decisions with adviser help. Parallel adviser fleets are optional
until the core game works.

## 11. Scenarios

Create three deterministic mock days:

- Normal Friday: commute, daytime, evening rush, nightlife.
- Rainy Friday: afternoon rain changes activity and recommendations.
- Event Day: synthetic Midtown, airport, and North Brooklyn spikes.

Each scenario contains:

~~~
id
name
start_date
num_days = 3
operating_start = 08:00
operating_end = 20:00
turn_hours = 3
turns_per_day = 4
total_turns = 12
fleet_size
starting_distribution
weather_profile
event_profile
random_seed
~~~

All MVP scenarios run for three consecutive dates from 08:00–20:00. A future
real-data scenario may use 2019-10-18 as day 1 and expose the original
17:00–17:30 idea as a benchmark interval inside day 1.

## 12. Game state and API

Game state contains:

~~~
game_id
scenario_id
current_time
turn_number
status
fleet
gross_revenue
repositioning_cost
net_earnings
trips_captured
trips_missed
current_forecast
player_prediction
adviser_recommendations
forecast_points
~~~

Statuses are awaiting_decision, replaying, and finished. Use an in-memory
dictionary for the MVP:

~~~
games: dict[str, GameState]
~~~

Required routes:

~~~
GET  /health
GET  /api/scenarios
POST /api/games
GET  /api/games/{game_id}
GET  /api/games/{game_id}/forecast
GET  /api/games/{game_id}/advisers
POST /api/games/{game_id}/prediction
POST /api/games/{game_id}/allocation
POST /api/games/{game_id}/reposition
POST /api/games/{game_id}/advance
GET  /api/games/{game_id}/results
~~~

The frontend starts a game with an empty request body. The backend defaults to
the deterministic `normal_friday` data set; scenario selection is not part of
the player-facing round flow yet. The low-level reposition input is:

~~~json
{
  "moves": [
    {"from_zone": "upper_west", "to_zone": "midtown", "taxi_count": 5}
  ]
}
~~~

Prediction input contains one non-negative demand estimate for each of the nine
zones. Allocation input contains the target idle taxi count for each zone and
must sum to the available idle fleet. Advance commits the prediction and
allocation, derives repositioning internally, simulates three historical hours,
processes pickups,
dropoffs, and reposition arrivals, calculates earnings, advances the clock,
and returns time changes, turn results, fleet state, zone results, and
game_over. Never expose future trips through an endpoint.

The initial player-facing score is forecast accuracy. Each round scores the
prediction independently in all nine zones, awarding 0–100 points per zone
from absolute error relative to realized demand; the round score is the mean
of those nine zone scores. Fare revenue, repositioning cost, idle time,
missed demand, and operational regret remain secondary metrics for now.

## 13. Project structure

Backend target:

~~~
backend/
    app/
        main.py
        models.py
        game.py
        data.py
        forecast.py
        advisers.py
        scenarios.py
        config.py
    tests/
        test_game.py
    pyproject.toml
~~~

Keep responsibilities clear. Do not add microservices or a dependency-injection
framework. The repository currently has a minimal FastAPI scaffold; expand it
incrementally.

Frontend target:

~~~
frontend/
    app/page.tsx
    app/game/[id]/page.tsx
    components/NycMap.tsx
    components/AdviserCard.tsx
    components/RepositionPanel.tsx
    components/TurnResult.tsx
    components/GameHeader.tsx
    lib/api.ts
    lib/types.ts
~~~

Use Next.js, TypeScript, Tailwind CSS, React state, and ordinary API calls. Do
not introduce Redux.

The UI has one focused gameplay surface with the day/round clock, progress and
forecast score, a prominent current-weather card, news and recent-demand
context, the nine-zone map, adviser recommendations, and the two round inputs.
Do not expose a scenario picker or a queued FROM/TO move editor in the player
flow. Use a dark navy/charcoal dashboard with taxi-yellow accents and
restrained rat humor.

## 14. Tests

At minimum test:

1. Fleet size does not change unexpectedly.
2. Busy taxis cannot be repositioned.
3. Repositioning taxis cannot take passenger trips.
4. Too many taxis cannot be moved.
5. Captured trips add revenue and make taxis busy.
6. Completed trips place taxis in dropoff zones.
7. Repositioned taxis arrive at the correct simulated time.
8. Repositioning reduces earnings.
9. Trips are missed when no taxi is available.
10. Future trips are absent from current state.
11. Each turn advances exactly three operating hours.
12. The game ends after 12 turns.
13. The calendar skips the 20:00–08:00 closed period between days.
14. Fixed seeds produce deterministic scenarios.

Also add an integration test that creates a game, inspects the initial state,
plans moves, advances one turn, and verifies money, time, and fleet changes.
When TLC is added, test outlier timestamps, mixed types, null/zero fees, and
renamed columns.

## 15. Build order and non-goals

Build in this order:

1. Keep the FastAPI scaffold runnable and add typed domain models.
2. Implement zones, scenarios, taxis, trips, deterministic generation, and the
   simulation core.
3. Implement all 12 turns and the API.
4. Build the frontend, SVG map, controls, advisers, forecasts, replay, and
   results.
5. Add the yellow-taxi TLC adapter and real historical replay.
6. Add LightGBM and optional adviser-fleet/regret evaluation.
7. Add NOAA/Stormy and GDELT/Gossip to the playable real-data state.
8. Add LLM tool-calling advisers, The Don, and human experiments.

Do not build the trained demand model or fictional event headline generator
yet. Also defer individual passengers/drivers, detailed traffic physics,
complex economics, demand reacting to player supply, authentication,
databases, Redis, Kubernetes, unnecessary websockets, reinforcement learning,
or an elaborate agent framework.

## 16. Definition of done

The first demo is complete when a developer can:

1. Run FastAPI and the frontend locally.
2. Start the October 2019 TLC replay from the frontend.
3. Start with approximately 130 taxis across nine zones.
4. See time, forecasts, and four adviser recommendations.
5. Plan and deploy idle-taxi repositioning.
6. Watch three hours simulate rapidly.
7. Capture real TLC trips, earn fares, and pay costs.
8. See taxis finish in destination zones and the fleet redistribute.
9. Repeat through all 12 turns.
10. Complete three 12-hour operating days and see final net earnings.

The longer-term research outcome is a point-in-time historical replay platform
combining NYC mobility, weather, and event signals to evaluate predictive
strategies and regime-aware ensembles.
