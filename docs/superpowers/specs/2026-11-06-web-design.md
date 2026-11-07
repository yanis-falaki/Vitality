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

Routes: `/` (Home, §11; the Reader was here until then), `/reader`, `/graph`, `/cards`, `/day/:date?`; the selected item is in the URL
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

## 10. Growing over time

The model grows by about 20 actions and 150–200 interactions a captured day. The app was tuned on a
week; at about four months (`web/tests/fixture_large.py`: 6 strivings, 25 activities, ~1,800
actions over ~90 days, ~12,000 interactions) every view stays calm and the look is unchanged:

- **Day:** the row of day tabs is replaced by `‹ <day> ›` arrows (previous and next days with
  actions; also the ← and → keys) and a calendar button: a month page (months from the first to the
  last with actions) whose days with actions are tinted in three soft steps of the action colour
  and can be picked. Lane colours stay distinct within a day (an activity keeps its colour unless
  another of that day's activities has it).
- **Long lists of actions** (Reader page, inspector, Cards): up to 20 stay flat by day; longer,
  grouped by month, then by week, newest first, all collapsed but the latest (and the one holding
  the selection), titled "212 actions since Oct 21". Cards' "+ Actions" therefore shows only each
  activity's latest week.
- **Reader's "when"**: a calendar heatmap (a cell per day of the model, a column per week, month
  names above), shaded in four soft steps of the page's level colour (`--striving-1…4`,
  `--activity-1…4` in the theme); a day with actions opens Day. It scrolls sideways inside itself
  when longer than the page, starting at the latest weeks.
- **Outline and Cards:** strivings fold in the outline; activities are ordered by `last_seen`,
  newest first; those last seen over 30 days before the model's last day go behind a quiet
  "Older · n" toggle in the outline (per striving), and are quieter cards in Cards and on a
  striving's page.
- **Graph:** a time range, All (default) / Last 30 days / Last 7 days, relative to the model's last
  day: strivings and activities always, actions and evidence only in range. Each (range, evidence)
  is laid out once, when first shown, in a worker (ids and edges sent, not the serialized graph);
  over 1,000 nodes ForceAtlas2 uses Barnes–Hut and 300 (over 3,000 nodes, 200) iterations. A range
  with more than 5,000 interactions shows the model with "Too much evidence for this range — pick a
  shorter range or select an action", and the selected action's own evidence on a ring around it.
  Crowded graphs draw smaller action and evidence dots. Measured on the hub (headless Chromium):
  last 7 days with evidence (~1,000 nodes) 1.0 s, last 30 days (~4,100 nodes) 3.1 s, the worst frame
  while arranging 17 ms; mounting the finished 4,000-node graph takes one ~0.4 s frame.
- **API:** `/api/model` no longer carries action bodies, operations or interaction summaries
  (actions list the interactions they cite by name); `GET /api/actions/{id}` gives an action's body
  and operations, `GET /api/interactions?from=&to=` the summaries of a range of days. The assembled
  model is cached on the (path, mtime, size) of every file behind it, checked at most every 2 s,
  sent gzipped with an ETag (a refetch of an unchanged model is a 304), and read once in the
  background on start. At four months: 96 KB gzipped (730 KB plain), ~0.1 s per check, cold start
  ~12 s (parsing YAML; `vitality_usermodel.formats` with libyaml's `CSafeLoader` would be ~7× faster).

## 11. Home

The app is opened daily, so `/` is now **Home**, the first tab (top bar and phone tab bar); the Reader
moved to `/reader`, and an old link to `/` with `?page=` or `?sel=` redirects there. Home is a page to
look at, not a tour: no explanatory text. It has no inspector; what it shows links into the views.
Top to bottom (on a desktop the changes are a column beside the day and the week):

- **Header:** the day's date (serif) and one status line: last capture, latest observation, model
  last updated (the latest committed session), operations waiting, the modeler's state. Stuck reads in
  the muted warn tone: no capture for over an hour though there were captures earlier today, the
  observer over an hour behind the captures, the modeler paused, waiting or not running. Beside it, a
  **search** (the `/` key focuses it): every word must appear in a striving's or activity's title or an
  action's summary, over the loaded model; Enter or a click jumps (striving or activity → its Reader
  page, action → its day, selected, the inspector open).
- **Today so far:** Day's lanes, compact (a sitting per column, Day's colours; the 8 activities with the
  most time, 5 on a phone, the rest counted), opening Day; "Probably now: <action>" when the latest
  action ended under 30 minutes ago. With no actions today: "Nothing captured yet today — last evidence
  <day>" (or "Nothing modeled yet" when there were captures today) and that day's lanes, labelled.
- **Where your time went this week:** per striving, folded open to its activities, the minutes of
  actions in the last 7 days (solid bar in the level colour) and the 7 before (a thin ghost bar, and
  the difference); activities serving none under "Not yet serving a striving". Time is the actions'
  spans; an action serving two activities counts for each (once for a striving both serve). With no
  actions in the last 7 days, the 7 days up to the last day with evidence, said so.
- **What changed in the model:** the modeler's commits, newest first, 6 at a time ("Show more"), under
  "<day>, <time> · session": per belief one line (title, opening its Reader page; in plain words: new,
  retitled from "A", became established, confidence 5 → 7, now serves / no longer serves "S", removed or
  merged into "B"), then "+N actions". Sessions newer than the previous visit (kept in localStorage,
  read and written in try/catch) carry a quiet "new".
- Room is left above "Today so far" for opportunities from an ambient agent; nothing stands in for it.

**API.** `GET /api/changes?limit=&before=<commit>`: `git log` in the model, commits whose subject starts
`modeler run `, each with its time, session (run id), the action files it added and changed, and items
from comparing the frontmatter of its changed `strivings/` and `activities/` files with its parent's
(`git diff-tree`, `git cat-file --batch`, parsed with `vitality_usermodel.formats.parse`); an activity
deleted while actions moved from it to another is "merged into" that one. Read-only git only; each
commit is read once, kept by its hash. `GET /api/pipeline`: `now` (the server's local time, or
`serve_fixture.py --now`), the newest capture (`<device>/<day>/<time>` folder names, UTC) and the newest
observation file, from directory listings only. Both fixtures have a history of modeler commits to
match their session records.

## 12. Day's evidence, and the changes in a box

- **Day, Actions | Evidence** (`?mode=evidence`, a switch in Day's heading): Evidence lists every
  interaction observed that day, from the observations folder, on every device, whether an action cites
  it or not; grouped into sittings by Day's rule, in Day's scrollable list. A row: time, device (small,
  muted), the first operation and "(+n)", a few thumbnails (with several screens, each screen's latest,
  labelled "1", "2"), and a quiet "not modeled yet" when no action cites it. Chips filter by device and
  to those not modeled yet. The arrows and calendar step between days with interactions; the lanes stay
  the actions'; the strip and the inspector show the selected interaction (screenshots marked with
  their screen and "focused" when the client captured several). Home's "Today so far" links to it
  ("Evidence", today, at the newest).
- **API.** `GET /api/interaction-days`: interactions per local day, from the file names of
  `observations/<device>/<UTC day>/` at the offset of one observation per folder (cached by the folder's
  time). `GET /api/days/<day>/interactions`: the observations of the UTC folders around that day whose
  start falls on it, with their operations (count, first) and shots (`interaction.json`'s per-shot
  `screen`, `screen_position`, `focused`, read leniently) and the actions citing them; `Evidence` gains
  the same `shots`.
- **Home's "What changed in the model"** scrolls inside its own box ("Show more" loads into it): on a
  desktop as tall as the column beside it and no taller than the window, kept in view as the page
  scrolls; on a tablet or phone at most 60% of the window.
