

Low Poly Rat

https://sketchfab.com/3d-models/low-poly-rat-2613ec608c454ee5b0f2d5efccd78766?utm_source=chatgpt.com


Rat Race
A multi-agent decision lab for finding useful signals in noisy historical data
Elevator pitch:
Rat Race puts you in charge of a tiny NYC taxi empire. Historical data is replayed in real time, but the future is hidden. Several AI analyst rats investigate different signals such as mobility, weather, events, and historical analogues, then advise you where to allocate your fleet. You make the final decision. The system forecasts what it expects to happen, then reveals the actual historical future and measures how good your decision was.
Underneath the game is a general framework for testing which signals, models, and combinations of analysts actually improve decisions under uncertainty.
That directly matches Voloridge's theme: large noisy datasets, figuring out what information actually matters, processing it at scale, and finding hidden patterns.

1. What the player is actually doing
You run:
Rat Cab Co. 🐀🚕
You have 100 taxis distributed around NYC.
The game begins at a real historical timestamp:
Friday, October 18, 2019
5:00 PM
Everything after 5:00 PM is hidden.
The screen shows the state of the city:
MIDTOWN       22 taxis
CHELSEA        8
SOHO          13
UES           15
...

Weather:
Rain increasing

Recent demand:
Midtown +16%
Chelsea +4%
SoHo -8%

Time until decision:
01:24
Then your analyst rats investigate.

2. The AI advisers
I would use four advisers, not ten.
Each represents a real quantitative strategy.
🐀 Twitch
Momentum Analyst
Question:
What is happening right now?
Uses:
recent pickup/dropoff growth
rolling demand
neighboring-zone flows
short-term acceleration
Example:
Midtown demand has accelerated for three consecutive intervals. I expect the trend to continue.

🐀 Stormy
Weather Analyst
Question:
Is the environment changing demand?
Uses NOAA:
rain
temperature
snow
wind
visibility
It compares the current situation to historical weather-conditioned demand.
Example:
Heavy rainfall usually increases taxi demand in Midtown by 21% during Friday evening rush hour.
NOAA gives you hourly weather observations with precipitation, temperature, wind, visibility, pressure, snow and many other variables.

🐀 Gossip
Events Analyst
Question:
Is something unusual happening in the real world?
Uses GDELT:
event counts
geographic proximity
unusual news intensity
event themes
Example:
Event activity near Madison Square Garden is 3.1× above the normal level for this hour.
GDELT provides global events, people, organizations, locations, themes and other signals extracted from news.

🐀 Grandpa
Historical Analogue Analyst
Question:
When have we seen something like this before?
Searches for similar historical states based on:
time
weekday
season
weather
recent demand pattern
geographic distribution
Returns:
I found 23 similar historical evenings. In 17 of them, Chelsea demand increased within 30 minutes.
This one is particularly useful because it's almost a nearest-neighbor / regime search.

3. What makes them "agents"
They should not just receive prompts containing the same dataframe.
Each gets analytical tools.
For example:
get_recent_demand(zone)
get_demand_momentum(zone)
compare_weather_condition(...)
find_historical_analogues(...)
measure_event_intensity(...)
compare_neighboring_zones(...)
Each adviser has a different subset.
So:
Twitch
 ├─ demand_momentum()
 ├─ flow_propagation()
 └─ neighboring_zones()

Stormy
 ├─ weather_effect()
 ├─ similar_weather_days()
 └─ conditional_demand()

Gossip
 ├─ event_intensity()
 ├─ nearby_events()
 └─ historical_event_effect()

Grandpa
 ├─ nearest_historical_states()
 └─ outcome_distribution()
The LLM's job is:
decide which analyses to run,
interpret the results,
make a recommendation,
explain it.
The LLM is not your forecasting model.
That distinction makes the project much stronger.

4. The player's decision
The advisers disagree.
You see:
TWITCH
Move +12 taxis → Midtown

STORMY
Move +8 Midtown
Move +6 Chelsea

GOSSIP
Move +14 → Chelsea

GRANDPA
Minimal repositioning.
Current distribution is already close
to historically optimal allocations.
You can inspect their evidence.
Then manually redistribute your fleet:
                BEFORE       AFTER

Midtown             22          32
Chelsea              8          18
SoHo                 13           7
UES                  15          12
...
And hit:
DEPLOY RATS

5. What happens after the player's decision
There are two separate things here.
This distinction is important.
A. Forecast
Before revealing history, your model predicts demand over the next, say, 30 minutes.
Example:
Predicted demand

Midtown     37
Chelsea     21
SoHo        10
UES         14
Then estimate your expected result:
Predicted passengers served     83
Expected idle taxis             12
Expected unmet demand            9
Repositioning cost              14

B. Historical reveal
Then you reveal what actually happened from 5:00 to 5:30 PM in TLC data.
ACTUAL

Midtown     43
Chelsea     17
SoHo         8
UES         19
NYC TLC provides real taxi and for-hire trip records, which gives you the ground truth for these historical replay rounds.
Now score the player's allocation using actual future demand, not model predictions.
That's critical.
Your model can be wrong.
History is the judge.

6. Don't model "the user changes taxi demand"
I would simplify your earlier idea here.
The user's choice doesn't change how many people historically wanted taxis.
Instead:
The decision changes how effectively the player's fleet serves that demand.
Much easier to defend.
So build a simple allocation simulator.
Suppose:
Chelsea

17 requests
18 taxis

→ 17 served
→ 1 idle
Midtown:
43 requests
32 taxis

→ 32 served
→ 11 unmet
The user's choice affects:
trips served
unmet demand
idle vehicles
repositioning distance
That's enough.

7. Your score
Keep the primary scoring formula understandable.
Something like:
Score=Revenue−RepositionCost−UnmetDemandPenaltyScore = Revenue - RepositionCost - UnmetDemandPenalty
Or even initially:
Score=TripsServed−0.2(Repositioned taxis)−0.5(Unmet trips)Score = TripsServed -0.2(\text{Repositioned taxis}) -0.5(\text{Unmet trips})
Exact coefficients can be tuned.
But your serious research metric should be:
Regret
After seeing actual future demand, compute the optimal allocation that could have been made with perfect foresight.
Then:
Regret=Scoreperfect foresight−ScoreplayerRegret = Score_{\text{perfect foresight}} - Score_{\text{player}}
Example:
Perfect hindsight       92.4
You                     84.1

Regret                    8.3
Lower = better.
That is a very clean way of comparing:
Human
Twitch
Stormy
Gossip
Grandpa
ensemble

8. The ML model
Don't overcomplicate this.
Use LightGBM
Input row:
(zone, timestamp)

hour
day_of_week
month

demand_t-15
demand_t-30
demand_t-60

rolling_mean_1h
rolling_mean_4h

neighboring_zone_demand

rainfall
temperature
wind
visibility

event_intensity

zone_id
Target:
Demandzone,t+30mDemand_{zone,t+30m}
LightGBM is ideal for a hackathon because:
fast
excellent tabular performance
little tuning
handles nonlinear interactions
feature importance is easy to expose
Don't build a transformer.
Don't build an LSTM unless everything else is already finished.

9. Make it probabilistic
A very nice stretch goal is not merely:
Midtown demand = 43
but:
Midtown

P10        29
Median     38
P90        51
Use LightGBM quantile regression.
Then your advisers can disagree about risk.
For example:
Midtown has higher expected demand, but Chelsea has much greater upside uncertainty.
That makes allocation decisions more interesting.

10. The underlying analytical engine
This is the actual technical project.
Build your data at:
(taxi zone × 15-minute interval)
For each row:
timestamp
zone_id

pickups
dropoffs
incoming_flow
outgoing_flow

lag_15m
lag_30m
lag_1h
rolling_avg

temperature
rain
wind
visibility

event_count
event_intensity
So instead of repeatedly scanning raw taxi trips, your game queries a compact feature store.
Pipeline:
TLC raw trips
       │
       ├── aggregate by zone / 15 min
       │
NOAA   │
 ──────┤
       │
GDELT  │
 ──────┤
       ↓
time-aligned feature table
       ↓
Parquet
       ↓
DuckDB / Polars
That is where you get your serious big-data story.

11. Historical replay engine
This part is especially good for quant-dev relevance.
Never let the game query future information.
Give every round a cutoff:
cutoff = "2019-10-18 17:00"
Every API must obey:
timestamp <= cutoff
Then:
17:00

current state available

17:00–17:30

HIDDEN
When the player locks in:
advance_clock(30 minutes)
Only then expose those observations.
That gives you a proper point-in-time historical simulation and forces you to think about lookahead bias.

12. Make the agents compete too
Each adviser also proposes its own fleet allocation.
So after the reveal:
🐀 RAT LEADERBOARD

1. Grandpa              91.2
2. YOU                  88.7
3. Twitch               87.3
4. Stormy               81.9
5. Gossip               72.1

Perfect foresight       95.4
Now you're not merely asking whether the AI gave good prose.
You are measuring its actual decision quality.

13. Add the really interesting model: The Chief Rat
Once individual advisers work, add one final agent.
🐀 The Don
Meta-adviser
The Don doesn't directly analyze taxi data.
It analyzes the advisers.
Its job is:
Who should I trust under the current regime?
Features:
rain severity
time of day
recent volatility
event intensity
historical analogue quality
recent adviser performance
Output:
Twitch       0.35
Stormy       0.40
Gossip       0.05
Grandpa      0.20
Combined recommendation:
At=∑iwi(t)Ai,tA_t = \sum_i w_i(t) A_{i,t}
This is essentially a mixture of experts.
Now your serious research question becomes:
Can a regime-aware combination of specialized signals outperform any individual signal?
That is much better than:
Can we predict taxi demand?

14. What you evaluate at the end
Your results page should contain four experiments.
Forecasting
Does your underlying model predict demand?
Measure:
MAE
RMSE
perhaps pinball loss for quantiles
Adviser performance
Across historical episodes:
Average regret

Twitch        14.2
Stormy        17.8
Gossip        21.3
Grandpa       13.6
Ensemble performance
Compare:
best individual
equal-weight advisers
The Don
Human + AI
Every HackMIT player generates a datapoint.
Compare:
Human alone
Human + adviser recommendations
AI alone
AI ensemble
You may discover something unexpected.
For example:
Humans use weather information well but systematically overreact to event/news information.
Or:
Humans outperform every individual adviser but lose to the regime-aware ensemble.
Don't predetermine the conclusion.
Whatever you actually observe becomes part of the project.
That satisfies the challenge's desire for meaningful analysis rather than merely polished presentation.

15. Frontend
I would make the main screen look roughly like:
┌──────────────────────────────────────────────┐
│ RAT RACE                 OCT 18 2019  17:00 │
├───────────────────────┬──────────────────────┤
│                       │ 🐀 TWITCH            │
│       NYC MAP         │ BUY MIDTOWN          │
│                       │ confidence: 78%       │
│   🚕 🚕    🚕         │                      │
│        🚕             │ 🐀 STORMY            │
│                       │ MIDTOWN + CHELSEA     │
│                       │ confidence: 84%       │
│                       │                      │
├───────────────────────┴──────────────────────┤
│ YOUR FLEET                                   │
│ Midtown     [-] 32 [+]                       │
│ Chelsea     [-] 18 [+]                       │
│ SoHo        [-]  7 [+]                       │
│                                              │
│             [ DEPLOY RATS ]                  │
└──────────────────────────────────────────────┘
After clicking:
big animated FAST FORWARD.
Then flows appear over the NYC map and the leaderboard updates.
That is your demo moment.

16. Tech stack
I would keep it boring and reliable.
Data
Python
Polars
DuckDB
Parquet
ML
LightGBM
scikit-learn
Agent layer
Python
your preferred LLM API
simple tool-calling loop
Do not spend 8 hours adopting an elaborate agent framework.
Backend
FastAPI
Frontend
Next.js
TypeScript
Mapbox / Deck.gl
Recharts
Deck.gl could make taxi flows particularly nice.

17. Backend endpoints
Something approximately like:
GET /scenario
GET /state/{scenario_id}

POST /analysis/momentum
POST /analysis/weather
POST /analysis/events
POST /analysis/analogues

POST /agents/run

POST /allocation/predict
POST /allocation/commit

POST /scenario/reveal

GET /leaderboard
Frontend shouldn't know anything about raw datasets.

18. What to actually build first
This order matters.
Phase 1: prove the data loop
Use TLC only.
Build:
raw trips
→ 15-min zone aggregation
→ historical replay
→ player allocation
→ reveal actual future
→ calculate score
If this isn't working, stop everything else.

Phase 2: forecasting
Train LightGBM:
past demand → next 30 min demand
Get forecast vs actual visualization working.

Phase 3: two advisers
Start with:
Twitch
Grandpa
because both only require TLC.
Now you already have:
human vs two AI strategies vs history
That is enough for a legitimate MVP.

Phase 4: beautiful frontend
Build:
NYC map
fleet sliders
adviser cards
countdown
fast-forward reveal
leaderboard
At this point, you have something demoable.

Phase 5: add NOAA
Build Stormy.
Weather joins are relatively straightforward.

Phase 6: add GDELT
Only do Gossip after everything else works.
GDELT integration is the easiest thing here to become an enormous time sink.
If it gets messy:
cut Gossip.
Three advisers are completely sufficient.

Phase 7: The Don
Train the meta-strategy / mixture-of-experts.
This is your strongest technical stretch goal.

Phase 8: collect human experiments
At the hackathon:
player decisions
advisor views
scenario
score
regret
time spent
Store anonymously.
Your presentation can update live.

19. What NOT to build
This is equally important.
Do not build:
individual simulated passengers
individual taxi drivers
1,000 LLM agents
an RL environment
an LSTM unless necessary
detailed NYC traffic physics
actual routing
realistic taxi pricing
complex economics
causal claims about news
20 advisers
None of those improve the core idea enough.

20. The naming
I'd use:
Rat Race
Then a more serious subtitle:
A Multi-Agent Signal Discovery and Decision Lab
On the website:
Can you beat the rats at finding signal in the noise?
Your rats:
🐀 Twitch: momentum
🐀 Stormy: weather
🐀 Gossip: events
🐀 Grandpa: historical analogues
🐀 The Don: ensemble/meta-agent
That's enough personality to be memorable without turning the whole project into a joke.

21. Your architecture in one diagram
              REAL HISTORICAL DATA

          TLC        NOAA       GDELT
           │           │           │
           └───────────┼───────────┘
                       ↓
               FEATURE PIPELINE
               Polars + DuckDB
                       ↓
               POINT-IN-TIME STORE
                       │
           ┌───────────┼─────────────┐
           ↓           ↓             ↓
       Momentum      Weather      Analogue
        signal        signal       search
           │           │             │
           ↓           ↓             ↓
        Twitch       Stormy       Grandpa
           └───────────┬─────────────┘
                       ↓
                    The Don
                       │
                       ↓

                 ┌──────────┐
                 │  PLAYER  │
                 └────┬─────┘
                      ↓
               FLEET ALLOCATION
                      ↓
                FORECAST MODEL
                  LightGBM
                      ↓
               EXPECTED OUTCOME

                    ⏩

              REAL FUTURE REVEALED
                      ↓
                  BACKTEST
                      ↓
             score / regret / insight
That diagram is basically the whole project.

And the serious one-line description for résumé / judges
Built an interactive multi-agent historical replay platform that combines NYC mobility, weather, and event data to evaluate competing predictive signals and resource-allocation strategies under uncertainty, with point-in-time backtesting and regime-aware signal ensembles.
While the actual demo is:
"You're the CEO of a rat taxi company. Four analyst rats are screaming conflicting advice at you. Who do you trust?"
That combination is exactly where I think this project gets interesting.


