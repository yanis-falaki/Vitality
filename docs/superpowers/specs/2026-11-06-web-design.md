# Vitality — Web app design (phase 3, read-only)

**Date:** 2026-11-06
**Status:** Agreed with the owner in conversation (views chosen from mockups); implementation delegated.

## 1. Purpose

A private web app on the VM for reading the user model: what it believes about the owner, why, and
the evidence behind it. Read-only: it never writes to the model. It is the phase 3 UI of the
user-model spec (§13), without editing for now.

The owner chose four views from clickable mockups, all sharing one inspector. The mockups are
throwaway HTML in the hub scratchpad; their look and behaviour are the reference (§6).

## 2. Scope

**In:** the `web/` module (API + React app), its Docker image and Compose service, the four views,
the inspector, the modeler's state in the top bar, `./vitality` mentioning `web`.

**Out:** editing (rename, lock, reassign, merge), feedback, search across evidence text, auth of its
own (exe.dev's private proxy is the access control), any write to `/model`, `/state` or `/captures`.

## 3. Decisions

| Question | Decision |
|---|---|
| Module | `web/`, beside `client`, `observer`, `modeler` |
| Backend | Python, FastAPI, reading the model through `vitality_usermodel` (one owner of the formats) |
| Frontend | React + TypeScript + Vite, TanStack Query, React Router; graph with Sigma + graphology (ForceAtlas2) |
| API types | Generated for the app from FastAPI's OpenAPI schema (`openapi-typescript`), committed |
| Storage | None. Every request reads the files (a small cache keyed on mtimes is allowed) |
| Access | exe.dev private proxy on an alternate port: `https://vitality-librarian.exe.xyz:4000/`. Never public; no `share` commands |
| Theme | One light, calm theme for every page, including the graph (§5) |

## 4. The API (`web/api`, package `vitality_web`)

Mounted read-only: `/model`, `/state` (the modeler's `~/vitality-data/modeler`), `/captures`.

- `GET /api/model` → strivings, activities, actions (frontmatter + body), plus per-interaction
  summaries the views need without fetching evidence: for each interaction referenced by an action,
  its start, end, operation count and screenshot count. Malformed files are skipped and listed in
  `problems: [{path, error}]`, never a 500.
- `GET /api/evidence/{interaction:path}` → the observation (meta + body), its operations, its
  screenshot file names (sorted), and the ids of the actions that cite it. 404 if unknown.
- `GET /api/modeler` → the modeler's `status.json` (or `{"state": "not running"}`), the operations
  waiting (untracked operations files in `/model`, read with `git` like the modeler's `repo.waiting`,
  or a plain count of files not in `git ls-files`), and the session records from `sessions.jsonl`
  (newest last; `offered` dropped).
- `GET /captures/{path}` → a screenshot from `/captures`. Only `.webp`, `.png`, `.jpg`; the resolved
  path must stay inside `/captures` (no `..`, no symlink escape) or 404. Long cache headers.
- Anything else → the built app (`index.html` for unknown paths, so client routes work).

`vitality-web serve` runs uvicorn on `0.0.0.0:${WEB_PORT:-4000}`. Times stay as the files hold them
(ISO 8601 local with offset); the app displays the local wall-clock text.

## 5. The look: one theme

Everything comes from one token file (`web/app/src/theme.css`, CSS variables) and a handful of
shared components; no view defines its own colours or fonts.

- **Calm:** warm off-white background, white panels, hairline borders, almost no shadow, generous
  whitespace, one radius scale, one spacing scale.
- **Type:** Inter for the interface; Source Serif 4 for the model's own prose (bodies, observations);
  JetBrains Mono for times and ids. Self-hosted (fonts bundled), no requests to Google at runtime.
- **Colour means level:** striving amber, activity teal, action blue, evidence grey, as muted tones
  checked for contrast on the background and for colour-blind distinguishability (use the `dataviz`
  skill's palette method and validator). Apart from these and status badges, the UI is neutral.
- **The graph is light** like every other page.
- Tentative vs established shown the same way everywhere (dashed vs solid accent).

## 6. The views

Routes: `/` (Reader), `/graph`, `/cards`, `/day/:date?`; the selected item is in the URL
(`?sel=<id>`) so a view can be shared and reloaded. A top bar switches views and shows the modeler's
state (`idle` / `running` / waiting, operations waiting, last session). The inspector (right panel,
~440 px) is the same component in every view.

Reference mockups (behaviour and look; take the ideas, not the code), in the hub scratchpad
`mock/`: `reader.html`, `graph.html`, `cards.html`, `timeline.html`, shared `common.js`/`common.css`.

- **Inspector:** level label + id; title; confidence dots; status; time span; links (serves,
  hinders, part of, relations) as chips that select; the body as Markdown with dated references
  (`2026-10-24/22-56-09`) and `act-`/`str-` ids turned into links; for an activity, its actions by
  day; for an action, its evidence: one card per interaction (time, its operations with the ones
  this action cites emphasised, thumbnails that open a lightbox, the observation behind a
  disclosure).
- **Reader** (built first): left outline (strivings, their activities, then "Not yet serving a
  striving"); the page of the selected striving or activity: serif title, byline, a strip of when
  its actions happened across the days, the body, then its activities or its actions by day.
  References and actions open in the inspector.
- **Graph:** Network layout only (ForceAtlas2, deterministic seed): strivings, activities, actions
  as circles sized and coloured by level; edges part_of / hinders / striving relations; "+ Evidence"
  toggle adds the interactions (labelled `time · first operation (+n)`). Click focuses the node's
  two-step neighbourhood and opens the inspector; hover highlights neighbours; search box jumps to a
  node. Labels: strivings and activities always; actions when zoomed in, never overlapping (label
  grid); legend with counts.
- **Cards:** strivings across the top, their activities beneath with curved links (dashed for a
  second striving, a "Not yet serving a striving" group), actions expanding under an activity by day;
  depth toggle (Strivings / + Activities / + Actions); drag to pan, wheel to zoom around the pointer,
  zoom buttons.
- **Day:** day tabs; heading with one summary line; lanes, one per activity of that day, the columns
  being the day's sittings (a gap over 30 min starts a new one), sized by length so idle time is
  squeezed out; the selected action's screenshots as a horizontal strip (small thumbnails, lightbox);
  activity filter chips (and clicking a lane name) filter the list and dim other lanes; the day's
  actions in a scrollable list grouped by sitting. The left pane fits the window: no page scroll,
  only the strip and the list scroll.

## 7. Packaging

- `web/pyproject.toml` (uv, hatchling, deps `fastapi`, `uvicorn`, `vitality-usermodel` by path),
  `web/vitality_web/`, `web/tests/`, `web/app/` (pnpm or npm, lockfile committed).
- `web/Dockerfile`: a Node stage builds `web/app` into static files; the final Python 3.14 slim image
  (as the other services) holds `vitality_web` and the built app; `git` installed (for waiting
  operations).
- `compose.yaml` service `web`: `<<: *common`, image `vitality-web`, command `vitality-web serve`,
  `ports: ["${WEB_PORT:-4000}:4000"]`, `WEB_PORT` fixed to 4000 inside, read-only binds of model,
  modeler state and captures (`create_host_path: false`), `/etc/localtime`, a healthcheck.
- `./vitality`: usage, `logs` and `status` mention `web` (status prints the URL). `.env.example`
  gains `WEB_PORT`. READMEs: `web/README.md`, a line in the top README.

## 8. Testing

- API: pytest against a small fixture model built in the tests (two strivings, a few activities and
  actions, observations, operations, a broken file, a fake captures tree with a symlink escaping it):
  each endpoint, problems listed, path traversal refused, unknown evidence 404.
- App: `tsc --noEmit`, `vite build`; a Playwright smoke run against the API serving the fixture
  model: every view renders without console errors, selecting an item fills the inspector, the Day
  pane has no page scroll at 1366×768; screenshots read by the implementer to judge the look.
- Never run against the owner's real data or the VM.

## 9. Build order (each a working app)

1. API, the shell (top bar, routing, theme, inspector), Reader.
2. Graph.
3. Cards.
4. Day, then Compose, `./vitality`, docs.

The owner looks after step 1 is deployed on the VM, and again at the end; merge to `main` on their word.
