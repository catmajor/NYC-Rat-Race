# NYC Rat Race API

FastAPI starter service for the NYC Rat Race project.

## Run locally

From this directory, create and activate a virtual environment, then install
the project with its development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Start the development server with:

```bash
uvicorn app.main:app --reload
```

The API is available at <http://127.0.0.1:8000>. Interactive documentation
is available at <http://127.0.0.1:8000/docs>.

## Test

```bash
pytest
```
