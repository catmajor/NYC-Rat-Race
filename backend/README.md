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
python -m pip install -e '.[dev]'
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

## Test

```bash
pytest
```
