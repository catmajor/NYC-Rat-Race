# NYC Rat Race

![NYC Rat Race gameplay](docs/game-visual.png)

## Inspiration

Machine-learning output and data visualizations can be difficult to make fun.
We wanted to build a game where weather, time, city events, ML, and AI agents
all directly affect the experience.

## What It Does

You manage a small NYC taxi company, except all of your drivers are rats. Use
the live map, weather, events, and four specialist rat advisers to decide where
to send your cabs. Each adviser focuses on a different source of information,
so you must decide which advice to trust before committing your allocation.

After each decision, an ML model predicts demand and the cabs move through the
city. Reassigned cabs follow shortest paths over NYC's road network.

## How We Built It

Yellow taxi trips from NY-TLC, NOAA weather, and GDELT news events feed a
point-in-time feature pipeline. LightGBM demand models are exported to ONNX
and forecast demand for each game zone without exposing future information.

The FastAPI backend runs the game loop, deterministic analysis tools, and four
specialist advisers. Gemini optionally turns each adviser's verified analysis
into personality-driven recommendations without controlling the numerical
results. DuckDB provides historical demand context.

The React frontend uses MapLibre GL and deck.gl for a 3D NYC map, animated
weather, event bubbles, rat drivers, taxi routing, and adviser interactions.

## Contributions

- Nikita: map, tiles, routing, and ML model
- Jolynn: UI and adviser-agent work

## Challenges

Filtering GDELT data down to a useful NYC scenario was challenging, and
downloading and processing the source datasets took substantial time.

## What We Are Proud Of

We built a playable 3D NYC map where rat cabs actually follow the city's road
network using Dijkstra shortest-path routing.

## What We Learned

We learned how to combine geospatial rendering, pathfinding, weather and news
datasets, game design, and ML forecasting. Time, location, weather, events,
sentiment, and recent demand together explained much of the variation in NYC
taxi pickups; the demand model reached roughly 10% validation error.

## What's Next

- Let more people play it
- Fix the inevitable rat-related bugs
- Add famous historical NYC days as playable scenarios

## Running It

The backend setup and test instructions are in [`backend/README.md`](backend/README.md).
Vercel deployment instructions are in [`DEPLOYING.md`](DEPLOYING.md).
