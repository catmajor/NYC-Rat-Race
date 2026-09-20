# Vercel Deployment

The repository contains the frontend, map assets, and precomputed routes. The
runtime ML and scenario artifacts are intentionally excluded from git. Vercel
downloads those artifacts during the build from a GitHub Release.

## 1. Build The Runtime Bundle

Run this from the repository root on a machine that has generated the ML and
scenario files:

```bash
node scripts/build-runtime-bundle.mjs
```

This creates:

- `runtime-artifacts.zip`
- `runtime-artifacts.zip.sha256`

The archive contains the ONNX models, model metadata, `pickups_15m.parquet`,
the 2019 NOAA station files, and the October 2019 GDELT file. These generated
files remain ignored by git.

## 2. Upload The Bundle

Create a GitHub Release and upload both files:

```bash
gh release create runtime-data --title "Runtime data" --notes "Generated production runtime artifacts"
gh release upload runtime-data runtime-artifacts.zip runtime-artifacts.zip.sha256
```

The release can be reused for later Vercel deployments. Rebuild and upload a
new release asset whenever the models or scenario data change.

## 3. Configure Vercel

Deploy two Vercel projects because the Python Function has a 225 MB bundle limit:

- The root project (`nyc-rat-race`) builds the FastAPI API.
- The `frontend/` project (`nyc-rat-race-frontend`) serves the Vite frontend and
  proxies `/api/*` and `/health` to the API project.

The frontend project is configured in `frontend/vercel.json`.

Set these project environment variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `RUNTIME_ARTIFACTS_URL` | Yes | URL of `runtime-artifacts.zip` from the GitHub Release |
| `RUNTIME_ARTIFACTS_SHA256` | Optional | SHA-256; otherwise the downloader fetches `${RUNTIME_ARTIFACTS_URL}.sha256` |
| `GOOGLE_API_KEY` | For Gemini | Server-side Gemini API key for live adviser agents |
| `GEMINI_MODEL` | No | Defaults to `gemini-2.5-flash` |
| `GEMINI_TEMPERATURE` | No | Defaults to `0.8` |
| `GEMINI_TIMEOUT_SECONDS` | No | Defaults to `20` |

The API build command is effectively:

```bash
node scripts/fetch-runtime-artifacts.mjs
cd frontend
npm ci
npm run build
```

The frontend project runs `npm ci && npm run build` with `dist` as its output.

After linking both projects once, deploy them in this order from the repository
root:

```bash
vercel --prod
vercel --cwd frontend --prod
```

The frontend project contains the API proxy target in `frontend/vercel.json`, so
future deployments do not require manually changing frontend API URLs.

Do not put `GOOGLE_API_KEY` in the frontend or commit it to the repository.

## 4. Verify

Test the Vercel build locally if the Vercel CLI is installed:

```bash
npx vercel build
npx vercel dev
```

Then check:

- `/` serves the frontend
- `/data/region_routes.json` serves the route artifact
- `/health` returns the API health response
- `/api/game/state` returns the initial game state
- `/api/advisers/twitch` returns a Gemini-backed response when `GOOGLE_API_KEY` is configured

Game sessions are intentionally demo-grade on Vercel: they are isolated by a
browser session header but remain in memory on a warm function instance. A cold
start resets the session.
