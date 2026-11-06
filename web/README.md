# Vitality web

A private, read-only web app for reading the user model: what it believes about you, why, and the
evidence behind it. It never writes to the model. The design is in
`docs/superpowers/specs/2026-11-06-web-design.md`.

Four views share one inspector (the panel that shows whatever is selected):

| View | Route | What it is |
|---|---|---|
| Reader | `/` | an outline of strivings and activities; each as a page to read, with a heatmap of when its actions happened |
| Graph | `/graph` | the model as a network, all of it or its last 30 or 7 days; "+ Evidence" adds the interactions; search jumps to a node |
| Cards | `/cards` | strivings, their activities and actions as a tree of cards; drag, pinch or wheel to move around |
| Day | `/day/<date>` | one day (arrows to the next and previous, a calendar for any other): a lane per activity across the day's sittings, the selected action's screenshots, its actions by sitting |

The selection is in the URL (`?sel=<id>`), so any view can be shared and reloaded. The top bar shows
the modeler's state. On a phone the views are in a tab bar at the bottom and the inspector opens as
a full-screen sheet; on a tablet it slides over the view.

## The service

`docker compose up -d` runs it as the `web` service: `vitality-web serve` on port `WEB_PORT` (4000),
with the model (`~/vitality-data/model`), the modeler's records (`~/vitality-data/modeler`) and the
captures (`~/vitality-data/server/captures`) mounted read-only at `/model`, `/state` and `/captures`.
Open it at `https://vitality-librarian.exe.xyz:4000/`: exe.dev's private proxy is its only access
control (it has no login of its own), so never make that port public. `./vitality status` prints the
address; `./vitality logs web` follows its log.

## The API

Nothing is stored. Parsed files are cached by path, modification time and size, and the assembled
model by those of every file it comes from (checked at most every two seconds); the model is read
once in the background on start. `/api/model` and `/api/interactions` are gzipped and carry an ETag,
so the app's once-a-minute refetch of an unchanged model is a 304. A malformed file is skipped and
listed in `problems`, never an error.

| Endpoint | Returns |
|---|---|
| `GET /api/model` | strivings and activities (frontmatter and body), actions (frontmatter without operations and body, the interactions they cite by name), `problems` |
| `GET /api/actions/<id>` | one action with its operation references and body; 404 if unknown |
| `GET /api/interactions?from=&to=` | a summary of each cited interaction started on those local days (both optional, included): start, end, operation and screenshot counts, first operation |
| `GET /api/evidence/<interaction>` | its observation, operations, screenshot file names and the actions citing it; 404 if unknown |
| `GET /api/modeler` | the scheduler's state (`status.json`, or `not running` when no modeler holds its lock), the operations waiting (untracked operations files, by git), the session records |
| `GET /api/health` | `{"ok": true}` (the container's healthcheck) |
| `GET /captures/<path>` | a screenshot (`.webp`, `.png`, `.jpg` only, never outside `/captures`), cached for a year |

Anything else is the built app (`index.html` for client routes). Times are passed through as the
files hold them (local time with its offset) and shown as that wall-clock time.

## Code

- `vitality_web/`: the API (`schema.py` is the contract; `model.py`, `modeler.py`, `captures.py` read
  the files; `app.py` serves; `main.py` is the command).
- `app/`: React + TypeScript + Vite. `src/theme.css` holds every colour, font and size; `src/components/`
  the shell, inspector and shared pieces; `src/views/` one file (and stylesheet) per view;
  `src/api/schema.ts` is generated from the API. Fonts (Inter, Source Serif 4, JetBrains Mono) are
  bundled; the app makes no request outside its own server.
- `tests/`: the API tests, `fixture.py` (a small fictional model: never the owner's data),
  `fixture_large.py` (about four months of fictional model; `test_large.py` times the API on it),
  `serve_fixture.py` and `smoke.py`.

## Develop

The API, from this folder:

    uv run pytest -q
    uv run pyright

The app, from `app/` (Node 22):

    npm ci
    npm run build          # tsc --noEmit, then vite build into app/dist

After changing the API's models, regenerate the schema and the app's types (a test checks they match):

    uv run vitality-web openapi > app/openapi.json && (cd app && npm run api)

To look at the app, serve the fixture model and either the build or Vite's dev server (which sends
`/api` and `/captures` to port 4000):

    uv run python tests/serve_fixture.py /tmp/vitality-fixture --port 4000
    (cd app && npm run dev)

The smoke run drives Chromium through every view at desktop, tablet and phone sizes (Playwright is
not a dependency; use a Python that has it):

    uv run python tests/serve_fixture.py /tmp/vitality-fixture --port 4100 &
    <python with playwright> tests/smoke.py --url http://127.0.0.1:4100 --shots /tmp/shots

With `--large` on both (`serve_fixture.py ... --large`, `smoke.py ... --large`) it runs on about four
months of model and also checks Day's arrows and calendar, the Reader's heatmap, the Graph's time
range, and that arranging the evidence never holds a frame over 100 ms.

It checks that each view renders without console errors, that selecting fills the inspector, that
no page scrolls sideways, and that the Day view fits a 1366×768 window; it saves a screenshot of
each view and size.

The image, from the repository root:

    docker build -f web/Dockerfile -t vitality-web .
