# Vitality — Sub-project 1: Screenshots → User Model

**Date:** 2026-10-21
**Status:** Draft for review

## 1. Context

Vitality is a long-term project: a persistent agent with a continuous view of its user's life (devices, later audio and camera) that proactively helps instead of waiting to be prompted through a chat box. The full vision decomposes into several sub-projects (sensing, interpretation, user modeling, proactive triggers, an acting agent, chat/messaging, evaluation).

This spec covers **only the first sub-project**: building an accurate, inspectable model of the user from laptop screenshots, with all processing off the host, designed so other input sources can plug in later. Everything else is explicitly deferred (§11).

The user-modeling approach is inspired by Stanford's GUM ("Creating General User Models from Computer Use", Shaikh et al., 2025). GUM is reference material only; all code is our own.

## 2. Goal and success criteria

**Goal:** From screenshots alone, maintain a set of natural-language beliefs about the user that are accurate, current, traceable to evidence, and editable by the user.

**Success after one week of normal use:**
- High-confidence propositions are mostly correct, as measured by the user's ✓/✗ marks in the view.
- The timeline shows observations that accurately describe the user's activity.
- Daily model spend stays within the $50–200/month budget.

**Primary user:** the author, as a single-user personal tool. Research and business uses come later, informed by this.

## 3. Glossary

| Term | Meaning |
|---|---|
| **Client** | A device running the sensor daemon (laptop in v0; desktop, phone later). |
| **Source** | One stream of input of one kind on a client (`screen` in v0; later `mic`, `camera`, …). A client may have several. |
| **Capture** | One unit of raw data from a source plus metadata (client, source, timestamp, trigger reason). A screenshot is a capture. |
| **Batch** | Captures from one source on one client over a fixed time window (~1 minute for `screen`). Grouped by fixed rules, no model. |
| **Interpreter** | The model that turns a batch into an observation. One per kind of source (a vision model for `screen`). |
| **Observation** | A structured text description of what happened, produced by an interpreter from one batch. The only input the engine consumes, regardless of source. |
| **Proposition** | One natural-language belief about the user, with reasoning, confidence, decay, and status. |
| **Confidence** | How sure the model is that a proposition is true (0–1, elicited from the model as 1–10). |
| **Decay** | How quickly a proposition goes stale (elicited 1–10; shown to the user as "shelf life"). Affects retrieval weighting, never deletes. |
| **Evidence** | Links from a proposition to the observations supporting it. |
| **Pinned** | A proposition the user has edited or retired. The engine never modifies pinned propositions. |
| **User model** | All propositions plus their history. |
| **Propose / Retrieve / Revise** | The engine's three steps (§6). |

Chain: client → source → captures → batch → interpreter → observation → propose / retrieve / revise → user model.

## 4. Architecture

Four components:

1. **Daemon** (laptop, Python) — captures screenshots, uploads them.
2. **Processing server** (Python) — ingest, batching, interpretation, engine. The only component that calls models.
3. **Storage** — capture store (files) and a shared SQLite database.
4. **View server** (TypeScript: Node + React) — reads and edits the database; serves the web UI.

The processing server and view server are separate processes ("data plane" and "management plane") sharing the database. In v0 all server-side components run on one always-on machine. Development starts with everything on the laptop; deployment moves server-side components to a cheap cloud VM.

**Networking:** all machines join a private Tailscale network (an encrypted WireGuard-based VPN). Nothing is exposed to the public internet; the view is reached at the server's Tailscale address. Plain WireGuard is the fallback if a third-party account is undesirable.

## 5. Daemon (client)

**Platform:** any Linux desktop. No compositor-specific code in v0.

**Input detection:** read input devices via evdev (`/dev/input`; user must be in the `input` group). Only event *timing* is used; key codes and pointer coordinates are discarded immediately and never stored or transmitted. Works identically on X11 and all Wayland compositors.

**Screen capture:**
- Wayland: xdg-desktop-portal ScreenCast → PipeWire stream, with a persisted restore token so the user approves once.
- X11: direct capture (XGetImage or equivalent).
- Captures every monitor the user approved in the portal prompt (all monitors on X11); each monitor is a separate capture with a `monitor` field. Images are downscaled and encoded as WebP.

**Capture policy:**
- **Burst captures:** after input activity settles (short debounce), take a capture; at most one burst capture every few seconds while activity continues.
- **Heartbeat:** one capture per minute regardless of input.
- Nothing while paused.
- All intervals configurable.

**Pause toggle:** a CLI command (`vitality pause` / `vitality resume`) the user can bind to any key, plus a visible indicator of capture state (tray icon or status-bar-friendly output). No denylist, no redaction in v0 — pausing is the only privacy control.

**Spool and upload:** captures are written to a local spool (SQLite + files) and uploaded in batches over HTTP to the processing server. Upload is retried until acknowledged; acknowledged captures are removed from the spool. The spool has a disk cap; oldest captures drop first if exceeded.

**Service:** runs as a systemd user service with restart-on-failure; reports a heartbeat to the server so a dead daemon is visible on the status page.

**Capture record:** `{capture_id, client_id, source: "screen", monitor, ts, trigger: "burst" | "heartbeat", image}`.

## 6. Processing server

### 6.1 Ingest
- HTTP endpoint, authenticated by a per-client token.
- Saves each capture to the capture store (content-addressed by hash) and its metadata to the database; acknowledges receipt.
- **Dedupe:** computes a perceptual hash and compares to the previous capture from the same client and source; near-identical captures are marked `unchanged` and excluded from batches (but retained in the store).

### 6.2 Batcher
- Fixed rule: every ~1 minute, take each client/source's new non-duplicate captures in time order and form one batch.
- Cap on captures per batch (e.g. 15); if exceeded, keep evenly spaced captures.
- No model involved.

### 6.3 Interpreter (screen)
- One vision-model call per batch, all of the batch's images in a single request with timestamps and instructions.
- Output (structured JSON) — the **observation**:
  - `summary` — what the user was doing
  - `apps_and_sites` — what was on screen
  - `salient_text` — important on-screen text quoted verbatim (messages, assignment names, dates)
  - `entities` — people, courses, organizations, projects
  - `signals` — intent, uncertainty, commitments, urgency
- Observations carry `source`, `client_id`, time range, and links to their captures.
- The observation format is source-agnostic so future interpreters (audio, etc.) produce the same shape.

### 6.4 Engine
Processes observations **one at a time, in time order**, in a single worker.

1. **Propose** (one text-model call): given the observation and a few preceding observations for context, reason about what it reveals about the user, then output 0–N candidate propositions, each `{text, reasoning, confidence 1–10, decay 1–10}`. Outputting nothing is valid and expected to be common.
2. **Retrieve** (no model): for each candidate, search active propositions with BM25 (SQLite FTS5), weight by recency according to decay, diversify with MMR, take the top ~5–10; union across candidates.
3. **Revise** (one text-model call): given candidates and related existing propositions, decide per proposition: keep, update (text/confidence/decay), merge, or contradict (confidence → 0, status hidden but retained); and which candidates are added as new.
   - Pinned propositions are never modified; if new evidence appears to conflict, the engine records a **conflict flag** for the user.
   - Engine writes use `WHERE pinned = 0` so a proposition pinned mid-revision is skipped — no further concurrency control is needed.

Every change is written to proposition history with its cause. Merged propositions inherit all evidence links.

Prompts are our own, informed by the GUM paper, versioned in the repo.

### 6.5 Model router
A config mapping each stage (`interpret.screen`, `propose`, `revise`) to a provider and model. v0 uses cloud APIs: a cheap vision-capable model for the interpreter and a cheap text model for propose/revise. Exact models chosen during implementation based on the golden set and measured cost.

### 6.6 Errors
- Model call failure: retry with backoff, then fall back to an alternate provider/model if configured.
- Invalid model output: rejected, never partially applied; the item stays queued and is retried.
- Backlog: batches are interpreted newest-first when far behind; the engine still applies observations in time order.

## 7. Storage

- **Capture store:** files on disk, named by content hash, encrypted at rest. Retained indefinitely; the view supports deleting a time range.
- **Database (SQLite, WAL mode):** shared by the processing server (writer of pipeline tables) and view server (reader; writer of user edits and marks).

Core tables (indicative):
- `clients`, `captures` (metadata, hash, unchanged flag), `batches`
- `observations` (+ links to captures)
- `propositions` (text, reasoning, confidence, decay, status, pinned, timestamps)
- `proposition_history` (every change with cause: observation, merge, or user edit)
- `evidence` (proposition ↔ observation)
- `conflicts` (pinned proposition ↔ conflicting observation)
- `marks` (user ✓/✗ per proposition, timestamped)
- `usage` (model calls, tokens, cost per stage)

The processing server owns the schema and migrations. The view server's TypeScript types are generated from the schema.

## 8. View

A React (TypeScript) web app served by a small Node server, reached over Tailscale. Single user, no accounts.

- **Propositions:** list of active propositions (search; sort by recency, confidence, shelf life; toggle to show retired/contradicted). Per row: confidence, shelf life, evidence count, last updated; actions ✓, ✗, edit (pins), retire (pins), unpin. Pinned rows show "set by you" and any conflict flag.
- **Proposition detail:** full fields, history, evidence → observations → capture thumbnails.
- **Timeline:** observations by day, expandable to their batch's captures — for judging interpreter quality.
- **Status:** per-client last capture / last seen / spool size, processing backlog, model cost today and this week by stage vs. budget.

User edits are written directly to the database. Edit and retire set `pinned = 1`; marks go to the `marks` table, which the engine never writes.

## 9. Evaluation

- **Golden set:** a few dozen of the user's own batches with hand-written expected observations, used to compare interpreter prompts and models.
- **Calibration:** ✓/✗ marks against confidence, reported on the status page.
- **Cost:** per-stage usage logging from day one; first-week measurement drives tuning of capture intervals, resolution, and batch caps.
- **Traceability:** every proposition traces to observations and captures, so "why does it believe this?" is always answerable.

## 10. Build order

1. **Daemon, saving locally** — starts accumulating real captures immediately.
2. **Processing server locally** — ingest, dedupe, batcher, interpreter; inspect observation quality on real data; build the golden set.
3. **Engine** — propose / retrieve / revise over accumulated observations.
4. **View.**
5. **Deploy** server-side components to a cloud VM, connect clients over Tailscale, run for a week, evaluate against §2.

## 11. Deferred (not in this sub-project)

- Fast trigger and proactive tasks; slow-pass planning
- Acting agent (doer) on its own VM, credential broker, approvals and standing permissions
- Chat / messenger (laptop notifications, phone chat), USER.md / MEMORY.md hybrid memory
- Additional sources (audio, camera, other devices, browser URLs, notifications)
- Privacy denylist or redaction beyond the pause toggle
- OCR gate, decision models (e.g. Jev, CLM), learned gates, parametric user models
- Fir HPC / self-hosted models
- Typed beliefs (goals, open loops), episodes, nightly consolidation
- Multi-user anything
