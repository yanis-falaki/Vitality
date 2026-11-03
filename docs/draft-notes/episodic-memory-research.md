# How existing systems turn screen activity into readable episodic-memory records

Research notes for Vitality. Compiled 2026-11-01 from primary sources where possible.

**Method and confidence.** For the open-source systems I cloned the repos (shallow) and read the code and prompts directly (Screenpipe, Dayflow, GUM, MIRIX, openai/codex templates). For papers and vendor docs I used the fetched pages; a fetch tool summarised those pages, so numbers quoted from papers are as reported by that summary of the paper and should be re-checked against the PDF before being relied on. Anything not confirmed from a primary source is marked **(unverified)**.

Commit snapshots read: Dayflow `730d6f8`, GUM `3317d94`, Screenpipe `a6225fa`, MIRIX `39e2411`.

## Contents

1. Screenpipe
2. Dayflow
3. GUM (General User Models)
4. Microsoft Recall
5. Rewind / Limitless
6. OpenAI: Chronicle / "Computer History" and Codex memories
7. VISTA
8. Other notable work: Activity Frames, FOCAL, MIRIX, SummAct, others
9. Synthesis

---

## 1. Screenpipe (mediar-ai/screenpipe)

Sources: [README](https://github.com/mediar-ai/screenpipe), `docs/EVENT_DRIVEN_CAPTURE_SPEC.md`, `crates/screenpipe-db/src/migrations/`, `crates/screenpipe-engine/src/routes/activity_summary.rs`, `crates/screenpipe-core/assets/pipes/*/pipe.md`, `crates/screenpipe-core/assets/skills/screenpipe-api/SKILL.md` (all in the repo).

### 1. Unit of a record

- **Stored unit: the frame** (not a summary). Capture is event-driven: triggers are app switch, click, typing pause, scroll stop, clipboard, and an idle fallback. The spec gives a 200 ms minimum interval per monitor and a 10 s maximum gap, with an idle capture every ~5 s (spec says "Medium" default; low/high profiles differ). Identical idle frames are de-duplicated by hash.
- **There is no LLM-written per-frame or per-interaction record.** Summaries are produced on demand or on a schedule by "pipes" (agents) that query the stored frames.
- Coarser units that exist: `meetings` table (audio-driven), `memories` table (see below), and an "episodes" concept in the workflow pipeline pipes (`workflow-*` pipes; I did not read the engine code for this in depth).

### 2. Contents of a record

Frame row (from `20260220000000_event_driven_capture.sql`): `timestamp`, `app_name`, `window_name`, `focused`, `browser_url`, `device_name`, `snapshot_path` (JPEG on disk), `accessibility_text`, `accessibility_tree_json`, `content_hash`, `simhash`, `capture_trigger`, `text_source`. A separate `elements` table holds per-UI-element text with role and source (accessibility vs OCR). Text comes from the OS accessibility tree first, with OCR (Apple Vision, Windows OCR, Tesseract) as fallback. FTS5 indexes the text. SQLite plus media files on disk.

`memories` table (`20260310000000_create_memories.sql`): `content`, `source` (default `user`), `source_context`, `tags` (JSON), `importance` (0 to 1), `created_at`, `updated_at`. The skill file says memories are "curated facts, preferences, decisions, project context" and tells agents to store only "genuinely useful long-lived" facts, "not transient observations". I found no automatic per-interaction memory writer in the repo; memories are written by the user or by pipes/agents through the API.

**Verbatim text selection (deterministic, no LLM).** `/activity-summary` returns `key_texts`. The SQL in `activity_summary.rs::key_texts_query` picks one representative string per (day, app, window) context:
- only `source = 'accessibility'` elements;
- length between 30 and 300 characters ("cap at 300 chars to skip marketing copy walls");
- excludes strings starting with `http` or `cdn.`;
- ranking: text-input roles (`AXTextArea`, `AXTextField`) first, then longer text, then most recent;
- afterwards de-duplicated case-insensitively, minimum 15 chars, and `LIMIT 20`.
The same response bundles per-app and per-window minutes (`WindowActivity {app_name, window_name, browser_url, minutes, frame_count}`), edited files, audio speakers, top memories, and a `data_status` field so an agent does not claim "no activity" when capture was off.

### 3. How context is supplied

Not as a rolling record context. The agent is given an `/activity-summary` for a time range (metadata aggregates plus bounded key texts/snippets), and escalates to `/search` only "for verbatim quotes / frame_ids". The skill rules say: start broad with activity-summary, use search for literal evidence, and "separate observed activity, explicit commitments, inferred open loops, and completed outcomes. Seeing a task or discussion is not evidence that the user performed or completed it."

### 4. Prompts (excerpts)

`day-recap/pipe.md` (schedule: manual). The task is a fixed output format plus a query budget:

> "Analyze my screen and audio recordings from today (last 16 hours). Read the screenpipe skill first. Use limit=10 per search, max 5 searches total. For app-usage totals, aggregate by app over the time range ... Only report what you can verify from the data."
> Format: `## Summary` (one sentence) / `## Accomplishments` (top 3, with timestamps, "Name specific apps, files, or projects") / `## Key Moments` / `## Unfinished Work` / `## Patterns`; ends with "**Next step:**".

`workflow-activity/pipe.md` (runs every 24 h, disabled by default) is an "evidence inbox" stage: it reads `/activity-summary`, then "turn[s] the useful timestamped snippets DIRECTLY into modest observations. Copy their literal text and metadata programmatically from the saved response." It also states "An app/window label alone is not an observed action."

Every pipe prompt also begins with a self-maintained `memory.md` of lessons (append-only, capped around 150 lines / 8 KB).

### 5. Short or fragmented activity

Handled at capture time, not summarisation time: debounce and a minimum interval, hash-based de-duplication, and the idle fallback. At query time, time is aggregated per app/window using an "active minutes" function over frame timestamps (`active_minutes`), so fragments are summed rather than merged into narrative units. I did not find a sessionisation step in the summary path.

### 6. Quality evaluation

`evals/coding-agent/` holds eval cases for the coding agent and some pipe workflows; there is also a `screenpipe-meeting-eval` crate. I did not find an evaluation of screen-activity summary quality **(unverified that none exists; I only read the directory listing and file names)**.

---

## 2. Dayflow (JerryZLiu/Dayflow)

Sources: [repo](https://github.com/JerryZLiu/Dayflow), files under `Dayflow/Dayflow/Core/AI/` and `Core/Analysis/`. License reported as MIT.

### 1. Unit of a record

A **two-level hierarchy**:
1. **Observations** (fine): produced from each analysis batch; "3-8 segments total" per batch, each with a start/end timestamp and a 1 to 3 sentence description.
2. **Timeline cards** (coarse, what the user reads): "one cohesive chunk of activity, roughly 15-60 minutes". Minimum 10 minutes per card, maximum 60.

Batching (`BatchingConfig.standard` in `AI/LLMTypes.swift`): analysis batches of **15 minutes** (`targetDuration`), split when a **gap exceeds 2 minutes** (`maxGap`); `createScreenshotBatches` in `AnalysisManager.swift` implements this. Card generation uses a **45-minute lookback** (`cardLookbackDuration`): existing cards overlapping that window are re-fed so that cards can be revised and merged, not only appended.

### 2. Contents of a record

`ActivityCardData`: `startTime`, `endTime`, `category`, `subcategory`, `title`, `summary`, `detailedSummary`, `distractions[]` (each with its own times, title, summary), `appSites {primary, secondary}` (canonical domains such as `docs.google.com`). Categories are user-defined and passed into the prompt.

- **Title**: "memory trigger", roughly 5 to 15 words, specific ("Fixed the infinite scroll crash on search results", not "Bug fixes"). Banned corporate words list ("research", "coordination", "workflow", "deep dive", ...). "Honest verbs" rule.
- **Summary**: 2 to 3 sentences, first person without "I", no third person, no "kicked off / dove into", no mental states.
- **Detailed summary**: a timestamped line list, "every app, every switch, every action", including "specific file/document names when visible, page titles, tabs, search queries".
- **Verbatim text**: not a first-class field. Exact strings appear inside the detailed summary and in the observation descriptions (see prompt below: "Exact file names, URLs, page titles ... search queries, messages ... numbers"). There is no quote-selection rule beyond "be specific".
- **Time range**: yes. **Goal/intent**: implicit in the title and "core intent" merge rule. **Entities**: app/site only.

### 3. How context is supplied

- To the observation (transcription) step: the video for the batch only; the prompt tells the model the exact clip duration. No history.
- To the card step: **the previous cards (JSON) from the last 45 minutes plus the new observations**. The prompt describes previous cards as "a draft you're revising and extending, not locked history". So context is a running, revisable summary, not the last N raw records.

### 4. Prompts (primary, from the repo)

Observation / transcription prompt (`AI/GeminiDirectProvider+Transcription.swift`), excerpt:

> "Screen Recording Transcription (Reconstruct Mode). Watch this screen recording and create an activity log detailed enough that someone could reconstruct the session. ... Capture: Exact app/site names visible (check menu bar for app name); Exact file names, URLs, page titles; Exact usernames, search queries, messages; Exact numbers, stats, prices shown. Bad: 'Checked email' / Good: 'Gmail: Read email from boss@company.com 'RE: Budget approval' - replied 'Looks good''. ... Segments: 3-8 segments total ... Group by GOAL not app (IDE + Terminal + Browser for the same task = 1 segment)."

Card prompt (`AI/GeminiDirectProvider+ActivityCards.swift`), excerpts:

> "You're writing someone's personal work journal ... The test: when they scan their timeline tomorrow morning, each card should make them go 'oh right, that.' Write as if you ARE the person jotting down notes about their day."
> "**When to start a new card:** 1. What's the main thing happening right now? 2. Does the next chunk of activity continue that same thing? -> Keep extending. 3. Is there a brief unrelated detour (<5 min)? -> Log it as a distraction, keep the card going. 4. Has the focus genuinely shifted for 10+ minutes? -> New card."
> "DEFAULT TO MERGING ... If you're unsure whether to merge or split, merge."
> "Your output cards must cover the same total time range as the previous cards plus any new observations ... Don't drop time segments that were previously covered."

Title / summary / detailed-summary instruction blocks are in `AI/GeminiPromptPreferences.swift` (`GeminiPromptDefaults`). Other providers (Claude, Codex, Ollama, Gemma, OpenAI-compatible) have their own prompt files (`ClaudeTimelinePrompt.swift`, `CodexTimelinePrompt.swift`, ...) which I did not read.

### 5. Short or fragmented activity

Explicit rules: brief detours under 5 minutes become `distractions` inside the surrounding card, not new cards; anything under 10 minutes is "folded into the neighboring card"; a new card requires the core intent to change for 10+ minutes; adjacent cards of the same work stream are merged on the next generation pass. Related sub-tasks (googling an error while debugging) are not distractions.

### 6. Quality evaluation

I found no evaluation harness or benchmark in the repo or README. There are unit tests for output validation and card boundaries (e.g. `DayflowTests/ClaudeActivityCardBoundaryTests.swift`) and retry/validation logic in the card generator (up to 4 attempts, with an "enhanced prompt" on validation failure). That is output-format checking, not quality evaluation.

---

## 3. GUM, General User Models (Stanford; Shaikh et al.)

Sources: paper [arXiv 2505.10831](https://arxiv.org/abs/2505.10831) (HTML v1: https://arxiv.org/html/2505.10831v1), repo [GeneralUserModels/gum](https://github.com/GeneralUserModels/gum): `gum/prompts/screen.py`, `gum/prompts/gum.py`, `gum/observers/screen.py`, `gum/batcher.py`, `gum/gum.py`. License MIT.

### 1. Unit of a record

**One record per interaction** (this is the closest match to Vitality's current design). The screen observer listens for mouse move/click/scroll (and keyboard) events, **debounces by 2 seconds** (`_DEBOUNCE_SEC = 2`), and captures a "before" frame (from a 10 fps rolling capture, `_CAPTURE_FPS = 10`) and an "after" frame. Each debounced interaction becomes one text `Update`. Updates are pushed to a persistent queue (`ObservationBatcher`, min batch 5, max 50) and are processed together by the proposer.

### 2. Contents of a record

Concatenation of two model outputs (`txt = transcription + summary`):
- **Transcription**: full markdown transcript of the before/after screenshots, verbatim and exhaustive.
- **Summary**: "a handful of bullet points" on specific user actions across the supplied images.
No explicit app/window/URL metadata fields, no inferred goal field, no entity list in the record. (The prompt does ask the transcript to "include all the application names, file paths, and website URLs".) The paper says the screen observer describes the user's actions "across up to 10 unique frames" alongside the content transcript.

### 3. How context is supplied

- Transcription call: the before and after images only.
- Summary call: **the last `history_k` = 10 screenshots** (rolling image history), plus the before/after images. Context for the summary is previous *images*, not previous text records.
- Downstream, the proposer gets the batched text of 5 to 50 records (joined as `[observer_name] content`), and a revise step sees clusters of related propositions together with their supporting observations.

### 4. Prompts (primary, from `gum/prompts/screen.py`)

> TRANSCRIPTION_PROMPT: "Transcribe in markdown ALL the content from the screenshots of the user's screen. NEVER SUMMARIZE ANYTHING. You must transcribe everything EXACTLY, word for word, but don't repeat yourself. ALWAYS include all the application names, file paths, and website URLs in your transcript. Create a FINAL structured markdown transcription."
>
> SUMMARY_PROMPT: "Provide a detailed description of the actions occuring across the provided images. The images are in the order they were taken. Include as much relevant detail as possible, but remain concise. Generate a handful of bullet points and reference *specific* actions the user is taking. Keep in mind that that the content on the screen is what the user is viewing. It may not be what the user is actively doing or what they believe, so practice caution when making assumptions."

Proposer prompt (`gum/prompts/gum.py`, `PROPOSE_PROMPT`) is where entity naming matters: "your analysis must **explicitly identify and refer to specific named entities** ... applications, websites, documents, people, organizations, tools ... Avoid general summaries--use exact names". It asks for at least 5 propositions, each with reasoning, a confidence score (1 to 10) and a decay score (1 to 10). It repeatedly warns that screen content is what the user is *viewing*, not necessarily doing.

### 5. Short or fragmented activity

The 2 s debounce collapses event bursts into one capture. Fragmentation is not handled at the record level; it is handled downstream by batching (5 to 50 records) and by the propose/revise/merge loop over propositions (similar propositions are clustered, merged, split).

### 6. Quality evaluation

Paper reports end-to-end results only (no isolated evaluation of the transcription/summary step, per the fetched paper text): an email-based study (N=18) with ~76% accuracy and Brier 0.17, and a screen-based "Gumbo" study (N=5) with 0.79 accuracy, Brier 0.28, and 25% of suggestions rated strong or excellent. The paper's screen observer used Qwen 2.5 VL 72B; propose/revise used Llama 3.3 70B. The repo's default model settings may differ **(unverified)**.

---

## 4. Microsoft Recall

Sources: [Microsoft Learn: Manage Recall](https://learn.microsoft.com/en-us/windows/client-management/manage-recall), [Support: Retrace your steps with Recall](https://support.microsoft.com/en-us/windows/retrace-your-steps-with-recall-aa03f8a0-a78b-4b3e-b0a1-2eb8ac48701c). Everything below the official docs is secondary reporting.

1. **Unit**: the snapshot (a screenshot of the active window), grouped on a timeline into "segments" (blocks of time with snapshots). Snapshots are taken "periodically while content on the screen is different from the previous snapshot" (Learn) / "every few seconds and when the content of your active window changes" (Support).
2. **Contents**: screenshot, OCR text (local), semantic embeddings in a vector database, and "associated information" such as the app and time (official docs do not list fields). Secondary reports describe a SQLite index with a `WindowCaptureTextIndex_content` table and an `ImageStore` folder **(unverified; community analyses of earlier builds)**. Click to Do analyses the opened snapshot on demand.
3. **Context**: none supplied to a summariser. Retrieval is search over text and visual embeddings, filterable by app.
4. **Prompts**: none public.
5. **Short/fragmented activity**: change-based capture and sensitive-info filtering. Official docs do not describe de-duplication or merging **(not documented)**.
6. **Evaluation**: none public that I found.

Key point for Vitality: Recall deliberately does **not** produce readable activity records; it produces a searchable index. It does not generate narrative summaries, per the official pages.

---

## 5. Rewind / Limitless

Sources are secondary (aggregator and news pages); I did not find vendor technical documentation, so treat as **(unverified)**. See [9to5Mac on the Meta acquisition](https://9to5mac.com/2025/12/05/rewind-limitless-meta-acquisition/).

- **Status**: Meta acquired Limitless (formerly Rewind) on 2025-12-05; the Rewind Mac app stopped capture on 2025-12-19, per press coverage. There is therefore no live system to inspect and no public prompts.
- **Rewind**: screenshots every second or two, OCR for search, audio transcription, heavy compression of the recordings; LLM features are meeting summaries and "Ask Rewind" question answering over retrieved text. Not an activity timeline of generated cards.
- **Limitless**: a wearable and app that transcribes audio, then generates summaries, action items and highlights after a meeting ("lifelogs"). Audio-centric; not screen-centric.
- Unit/contents/context/short-activity/evaluation: not publicly documented.

---

## 6. OpenAI: "Chronicle" (now "Computer History") and Codex memories

Sources: [Computer History docs](https://learn.chatgpt.com/docs/customization/computer-history) (the old `developers.openai.com/codex/memories/chronicle` URL redirects here), [Codex Memories docs](https://learn.chatgpt.com/docs/customization/memories?surface=app), [openai/codex](https://github.com/openai/codex) templates, [Neowin coverage](https://www.neowin.net/news/openai-introduces-chronicle-for-codex-a-recall-like-memory-feature-for-developers-on-macos/) of the April 2026 launch.

**What Chronicle is.** An opt-in research preview in the Codex macOS app (announced April 2026) that adds screen context to Codex memories. Press coverage described it as capturing screen context locally, uploading captures for processing, then writing local memories, with captures deleted after ~6 hours **(secondary sources)**. The official docs now describe its successor, **Computer History**, as having "replaced the earlier Chronicle preview".

**Computer History, per the official page:**
1. **Unit**: an interaction-event stream (clicks, typing, shortcuts, app switches, plus context from macOS accessibility). It states it does **not** include screenshots, microphone or system audio. Output units: "timeline items" and Markdown memory files.
2. **Contents**: a timeline item can show a title, a text summary, contributing apps, and a suggested skill or automation when repeatable work is detected. Memory files are plain Markdown stored under `$CODEX_HOME/memories/extensions/skysight/`. No finer schema is documented.
3. **Context**: "an ephemeral Codex session with access to the event stream creates these summaries" periodically. Temporary event files are kept up to 48 hours.
4. **Prompts**: not public. In `openai/codex`, the only trace of Chronicle I found is a `chronicle` boolean in `codex-rs/core/config.schema.json`. The capture/summary code is not in the open repo.
5. **Short/fragmented activity**: not documented.
6. **Evaluation**: none public.

**Adjacent, public: Codex's chat-memory pipeline** (not screen-based, but it is an episodic-memory writer with public prompts): `codex-rs/memories/write/templates/memories/stage_one_system.md` (Phase 1, one rollout at a time) and `consolidation.md` (Phase 2, global). Phase 1 emits `{"rollout_summary", "rollout_slug", "raw_memory"}` and has an explicit **no-op gate**: "Will a future agent plausibly act better because of what I write here?" If not, return empty. Rollout summaries are task-structured (`## Task <idx>: <name>`, `Outcome: success|partial|fail|uncertain`, "Preference signals" with evidence-to-implication bullets that preserve short near-verbatim user quotes). Relevant pattern: per-episode extraction followed by a separate consolidation pass, plus an outcome label and a "skip if nothing durable" rule.

---

## 7. VISTA (vista-research.github.io)

Does **not** relate. The page describes a visual harness that lets a multimodal model play ARC-AGI-3 games from raw 512x512 frames, with "lossless visual memory" (every returned frame retained and re-inspectable). It does not concern screen recordings of a person, activity summarisation, or episodic memory records. The only loose connection is the idea of keeping raw frames addressable instead of summarising them. Source: [vista-research.github.io](https://vista-research.github.io/) (summary of the fetched page).

---

## 8. Other notable work

### 8a. Activity Frames (arXiv 2608.05784), deterministic screen-activity compilation

[Paper](https://arxiv.org/html/2608.05784). The most directly relevant research I found on sessionisation.

1. **Unit**: an *activity frame* keyed by (application, site), built per monitor. Segmentation is deterministic, with **no model in the loop**.
2. **Contents**: `id, app, site, start, end, duration_min (active), wall_min`; `pages` (typed references from URL parsing: kind, entity, count); `input` (keystroke and click counts, typed text excluded by default); `interruptions` (app, seconds); `evidence` pointers to raw snapshot rows; plus document-level `coverage`, `blind_spots`, `provenance`. An optional "tier 2" `inferred` block is confidence-tagged and evidence-linked (not implemented in the reference code). Verbatim screen text is not part of the record; OCR output stays local.
3. **Context**: a compact plain-text context block (~1,469 tokens for a full day) for system prompts, plus an MCP server with six tools (`get_context`, `get_activity`, `get_day_summary`, `get_patterns`, `get_communications`, `get_steps`).
4. **Prompts**: none for compilation. QA agents had no tools; their prompts are not given.
5. **Fragments**: each snapshot earns dwell credit of min(gap, 90 s); a gap over 300 s closes the frame and is reported as a coverage gap; **flicker merge**: A -> B -> A where B lasts at most 20 s collapses into one A frame, with B recorded as an interruption (time not added to A). Single-snapshot transits stay visible; omitted frames are disclosed.
6. **Evaluation**: 64 questions over 8 days (single user, 61 days of data). Reported QA accuracy (Sonnet 4.5 / Opus 4.5): raw rows 82.1% / 91.1%; **LLM summary 66.1% / 80.4%**; activity frames 98.4% / 98.4%. Token cost for a day: 126,812 raw rows to 1,469 for the context block. Caveats stated by the authors: single user, and the frames arm was given a precomputed per-app ledger, which favours it on quantitative questions. The takeaway worth noting: in this study, free-form LLM summaries lost facts that structured records kept.

### 8b. FOCAL (arXiv 2604.19541), task-isolated desktop summarisation

[Paper](https://arxiv.org/html/2604.19541). A multi-agent, on-device pipeline.

1. **Unit**: a *session* of ordered actions (each = foreground app + window title + screenshot), re-grouped into **latent tasks**. One summary per task; the session summary is the ordered concatenation.
2. **Contents**: per-action memory-grounded descriptions (only for sampled actions), appended to the memory store of the task they belong to; one summary per task.
3. **Context**: the key design point. A text-only "Brain" agent first makes one pass over the *metadata only* and assigns each action a task ID, a continue/start flag, and a flag for whether to look at the screenshot. When the VLM describes a sampled action, it receives the screenshot, metadata and **the earlier descriptions routed to the same task**, not the last N chronological records. "The key invariant is that each M_k contains only evidence routed to task k."
4. **Prompts**: paper states none are included; no schema beyond the per-action tuple.
5. **Fragments / interruptions**: task stores can be non-contiguous so A -> B -> A returns to A's memory. No specific rule for short activity.
6. **Evaluation**: DesktopBench (reconstructed from VideoGUI; 420 sessions incl. 100 A -> B -> A interruption sessions), qwen3:8b on an M4 MacBook. Reported: Task Acc under interruption 0.03 (naive baseline) vs 0.81; G-Eval 2.96 to 4.16 (multi-task); VLM-call reduction ~72% and token cost reduction ~60%. Ground-truth descriptions were generated by GPT-5, which limits what the numbers say about human-judged quality.

### 8c. MIRIX (Mirix-AI/MIRIX; arXiv [2507.07957](https://arxiv.org/abs/2507.07957))

Multi-agent memory system with six memory types; it has a screen-monitor mode. Primary source read: `mirix/prompts/system/screen_monitor/episodic_memory_agent.txt`, `mirix/schemas/episodic_memory.py`.

1. **Unit**: one screenshot per second (idle frames discarded); "when the screenshots are accumulated to a certain amount" (the prompt says **20 screenshots**) they go to the episodic agent. Records are *events* that are **updated in place**.
2. **Contents** (`EpisodicEvent`): `event_type`, `summary` (short), `details` (long; "record as many details as possible"), `actor`, `occurred_at`, embeddings of summary and details.
3. **Context**: the system prompt shows **the 50 most recent and 50 most relevant events** with their IDs.
4. **Prompt rule worth copying**: "Use `episodic_memory_merge` for minor updates to existing events (e.g., user continues watching the same movie). Note that the new summary will overwrite the old summary, so ensure it covers both earlier and new information. Use `episodic_memory_insert` when significant changes occur ... Use `episodic_memory_replace` if you need to consolidate repeated items or rewrite overly long summaries ... Avoid appending to events with more than 5000 characters in details." Exactly one function call per batch, or none if nothing is new.
5. **Fragments**: handled by the merge-vs-insert decision against the open event; there is no fixed time window.
6. **Evaluation**: paper reports ScreenshotVQA (~20,000 high-resolution screenshots per sequence): +35% accuracy vs a RAG baseline with 99.9% less storage; and 85.4% on LOCOMO (text). Episodic record quality is not evaluated separately. The repo contains versioned prompt variants under `evals/prompts/` (0130a ... 0208b) used for evaluation **(I did not read the evaluation scripts)**.

### 8d. SummAct (arXiv [2410.08356](https://arxiv.org/abs/2410.08356))

Summarises sequences of UI element-level actions (element category, label, operation) into user intention. Two steps: an in-context LLM groups low-level actions into **sub-goals**, then a fine-tuned LLM (Mistral-7B) writes the **overall intention** from actions plus sub-goals; a loss term upweights tokens carrying action details (lambda = 2) so element content is not dropped. Evaluation on Mind2Web and MoTIF with cosine similarity: full method 0.755 to 0.842 vs 0.348 to 0.380 for an un-tuned LLM on Mind2Web. Relevant pattern: **sub-goal layer between actions and intent**. Input is structured UI traces, not screenshots.

### 8e. Others (mentioned by search results; not read deeply, mark **unverified**)

- OpenClaw "Logbook" plugin: Dayflow-like two-stage (periodic snapshots to timestamped observations to timeline cards), default 15-minute analysis window (could not fetch docs).
- ScreenTracker, FocusLens, ScreenMind: small open-source screen-journal projects with periodic or change-based capture (not read).
- OmniQuery, EgoSelf (arXiv 2604.19564), "A Grounded Memory System for Smart Personal Assistants" (arXiv 2505.06328): personal-memory work on media/egocentric video rather than desktop screens (not read).

---

## 9. Synthesis

### 9.1 Main design patterns across systems

| # | Pattern | Seen in |
|---|---------|---------|
| P1 | **Two layers: fine evidence, coarse readable record.** The thing a person reads is never the per-burst note. Fine records are inputs; readable records are rollups over 10-60 minutes or per task. | Dayflow (observations to cards), Screenpipe (frames to on-demand recap), Activity Frames, FOCAL (actions to task summaries), Codex (rollout to consolidation), SummAct (actions to sub-goals to intent) |
| P2 | **Per-interaction text is a transcript, not a story.** GUM writes an exhaustive verbatim transcription plus a short action summary, then lets downstream steps do the interpretation. | GUM, Dayflow observations |
| P3 | **Context = running, revisable summary, not the last N raw records.** Previous cards are re-fed and may be rewritten. | Dayflow (45-min lookback, "draft you're revising"), MIRIX (merge into an open event; overwrite summary so it covers old + new) |
| P4 | **Context = the same task's history, not the chronological neighbours.** | FOCAL (task-isolated memory) |
| P5 | **Explicit merge/split rules for fragments.** Detour under ~5 min is a "distraction" inside a card; under 10 min folds into a neighbour; flicker A-B-A with B <= 20 s merges; gap > 2 min (Dayflow batching) or > 5 min (Activity Frames) closes a unit. | Dayflow, Activity Frames, MIRIX (merge vs insert) |
| P6 | **Metadata from the OS, not from the model.** App, window title, URL, input counts, dwell time come from capture. The LLM interprets; it is not asked to read the menu bar. (Dayflow's prompt has to say "check the menu bar for the app name", which shows the cost of not having metadata.) | Screenpipe, Activity Frames, FOCAL, Dayflow (only partly) |
| P7 | **Verbatim text is chosen by rule or by role, rarely by "what looks interesting".** Screenpipe picks one string per window, preferring text the user typed (input fields), 30 to 300 chars, longest, most recent. Dayflow asks for exact filenames, URLs, titles, queries and messages. GUM transcribes everything and names entities. | Screenpipe, Dayflow, GUM |
| P8 | **Anti-vagueness rules in the prompt.** Dayflow: specific titles, banned filler words, "honest verbs", good/bad examples. GUM: "reference *specific* actions", cite named entities. | Dayflow, GUM |
| P9 | **Epistemic caution about screen content.** "What's on screen is what the user is viewing, not necessarily doing." Observed vs inferred vs completed are separated. | GUM, Screenpipe skill rules, Activity Frames (tiered `inferred` block) |
| P10 | **No-op / skip gates.** Don't write a record when nothing new or durable happened. | MIRIX ("if there is absolutely nothing new ... do not make any function calls"), Codex stage one |
| P11 | **Evaluation is rare and mostly end-to-end.** Only Activity Frames, FOCAL and MIRIX report numbers about record quality, using QA accuracy or LLM-judged benchmarks. Dayflow and Screenpipe publish none for summary quality. | -- |

A caution from the one study that compared representations directly (Activity Frames, single user, author-built): free-form LLM summaries scored lower on question answering (66 to 80%) than structured records (98%). That suggests keeping structured fields and evidence pointers alongside any narrative text, but the study is small and favours its own method.

### 9.2 Which patterns address the owner's complaints

| Complaint | Patterns that address it | How |
|-----------|--------------------------|-----|
| **Too short** | P1, P2 | In every system the short note is the *intermediate* artifact. Readable records are longer (Dayflow: summary + detailed timestamped list; MIRIX: separate `summary` and `details`; GUM: full transcript + bullets). Length comes from the record covering more time or carrying a details field. |
| **No sense of overall activity / lacking context** | P1, P3, P4, P5 | A record that spans an activity (card / task / open event) gives the overview that a 5-second burst cannot. Giving the model the *current open activity summary* (Dayflow, MIRIX) or the *same task's history* (FOCAL) instead of the last 15 raw notes tells it what the burst is part of. GUM, notably, hands the model previous *screenshots* only, which would not fix this. |
| **Random details / random quotes** | P6, P7, P9 | Pick quotes by role or rule: what the user typed/sent, the title or filename that identifies the object, error messages, the line under the cursor/selection, headings that name the thing. Screenpipe's rule (typed text first, 30 to 300 chars, one per window) and Dayflow's list (file names, URLs, titles, queries, messages, numbers) are concrete examples. Tag each quote with its role so the reader can tell why it is there. |

### 9.3 Candidate designs for Vitality

The three designs differ mainly in *what one record covers* and *who decides the boundaries* (the LLM, a rule, or a hybrid). They can be combined; each is described independently. Costs are rough and depend on the model.

#### Design A: Open-episode record (MIRIX / Dayflow style), updated per interaction

- **One record covers**: one *episode*, a coherent stretch of activity (a task or topic), which may span many interactions and minutes to an hour. Each new interaction either **extends** the open episode (rewriting its summary and appending evidence) or **starts a new one**. A closed episode stays as a fixed record.
- **Contains**:
  - `time_range`, `apps/windows/urls` (from capture, not the model), `interaction_count`;
  - `title` (specific, 5 to 15 words);
  - `goal` (one sentence: what the person seems to be trying to do; marked inferred when it is);
  - `summary` (3 to 6 sentences, running: covers the whole episode so far);
  - `progress` (what changed in this interaction: opened/edited/ran/sent);
  - `evidence`: 3 to 8 `>` quote lines, each tagged by role (`typed`, `title`, `error`, `reading`, `result`), and each replaced or kept when the episode is rewritten;
  - `open_threads` (optional: things visibly unfinished).
- **Context given to the model**: the current open episode (full text) plus the last 2 to 3 closed episode titles/summaries; the new interaction's screenshots and OS metadata. No raw last-15 list.
- **Prompt sketch**:
  ```
  You maintain a journal of what the user is doing. You get (a) the OPEN EPISODE so far, (b) the previous episodes (titles + one line each), (c) a new burst of activity: app/window/url, input counts, screenshots.
  Decide: EXTEND the open episode, or START a new one. Start a new one only if the main goal changed, not for a detour under 5 minutes (record detours inside the episode as "detour: ..."). If unsure, extend.
  Output the full updated episode: title, goal, summary (must cover old and new, 3-6 sentences, name the specific files/people/pages/errors), progress (this burst only), evidence quotes.
  Evidence rules: quote verbatim; prefer (1) text the user typed or sent, (2) the title/filename/identifier of the thing being worked on, (3) an error or result, (4) text they were reading that explains the goal. Tag each quote with its role. At most 8 quotes per episode; drop quotes that no longer help identify the activity. Never quote UI chrome, ads or navigation.
  Screen content is what was visible, not necessarily what the user did; mark guesses as "likely".
  If nothing new happened, output NO_CHANGE.
  ```
- **Trade-offs**:
  - (+) Directly answers "no sense of the overall activity"; one record per activity reads like a journal; evidence set stays curated rather than accumulating random lines.
  - (+) Single model call per interaction, similar to today.
  - (-) Rewriting the summary each time can drift or lose earlier facts (MIRIX needs an explicit warning that the new summary must cover old + new). Mitigation: keep immutable per-interaction raw notes and the episode as a view over them.
  - (-) The model decides boundaries, so wrong merges are possible; boundary quality is hard to check without labelled data.
  - (-) Records are not final until the episode closes; consumers must tolerate revisions.

#### Design B: Two-tier, per-interaction evidence note plus delayed episode card (Dayflow style)

- **One record covers**: Tier 1 is a short, factual **evidence note** per interaction (essentially today's observation, but structured and metadata-backed). Tier 2 is an **episode card** for a 10 to 60 minute window, produced later by a text-only call over the tier-1 notes (e.g. every 15 minutes, or when idle for N minutes), with a lookback window so adjacent cards can be merged and rewritten.
- **Contains**:
  - Tier 1: `time`, `app/window/url/input counts` (from capture), `action` (1 to 2 sentences, precise verbs), `quotes` (up to ~5, role-tagged, verbatim, chosen under the rules above, or chosen by a rule from accessibility/OCR text and merely *ranked* by the model);
  - Tier 2: `time_range`, `title`, `goal`, `summary` (2 to 4 sentences), `detailed_timeline` (timestamped lines referencing tier-1 notes), `key_quotes` (the 3 to 6 that identify the work, selected from all tier-1 quotes), `detours`, `apps`.
- **Context given to the model**: Tier 1 gets the current card's title/summary only (not the last 15 notes), so it knows what the burst belongs to. Tier 2 gets previous cards from the last 45 minutes plus all new tier-1 notes (Dayflow's pattern).
- **Prompt sketch (tier 2)**:
  ```
  You are writing the user's work journal. Input: previous cards (a draft you may revise) and new evidence notes with timestamps.
  Produce cards of 10-60 minutes. Default to merging; a new card needs the core intent to change for 10+ minutes; detours under 5 minutes go in "detours". Cover the whole time range; do not drop earlier time.
  Each card: title (specific, a memory trigger, no filler words), goal, summary (2-4 sentences), timeline (timestamped lines), key_quotes (3-6 verbatim lines chosen from the notes' quotes that best identify what the work was about, each with role).
  Use only facts from the notes. Do not infer motives beyond "likely".
  ```
- **Trade-offs**:
  - (+) Most robust to bad short-burst notes: tier-1 errors get corrected when tier 2 sees neighbours. Tier-2 quote selection sees the whole episode, so quotes can be chosen *relative to the activity*; this targets "random quotes" most directly.
  - (+) Easy to evaluate: tier 1 can be audited against screenshots, tier 2 against tier 1.
  - (+) The raw tier-1 notes keep the fine-grained evidence for later retrieval or re-synthesis with a better prompt.
  - (-) Latency: readable cards appear minutes after the activity.
  - (-) Two prompts and two stages to maintain; more storage and an extra call per window.
  - (-) Tier 1 still has the "few seconds of activity" problem; if it hallucinates, tier 2 can compound it unless tier 2 is told to ignore unsupported details.

#### Design C: Deterministic skeleton, LLM fills in the narrative (Activity Frames + Screenpipe style)

- **One record covers**: a **session** computed by rules: consecutive interactions in the same app/site (or same window/document) with a flicker merge (A -> B -> A with B <= ~20 s) and a gap threshold (e.g. > 2 to 5 minutes closes it). Boundaries come from code, not the model.
- **Contains**:
  - from code: `start/end`, `active_minutes`, `app/window/url/file`, `input counts`, `interruptions[]`, `interaction_ids`;
  - from code, by rule: **candidate quotes** (Screenpipe-style: text-input element content, 30 to 300 chars, de-duplicated, one per window, most recent), with source role;
  - from the LLM: `title`, `goal`, `summary` (3 to 5 sentences), and a choice of which candidate quotes to keep (it may only choose from the candidates, so it cannot invent quotes).
- **Context given to the model**: the skeleton for this session, the screenshots (or a sample of 3 to 6) for visual interpretation, and the one-line summary of the previous 2 to 3 sessions.
- **Prompt sketch**:
  ```
  Facts (from the OS, do not contradict): app, window, url, duration, input counts, interruptions.
  Candidate quotes (verbatim, numbered): [1] ... [2] ...
  Previous sessions: one line each.
  Write: title (specific), goal (what the person was trying to do, "likely" if unsure), summary (3-5 sentences using the facts), keep: list of quote numbers that best identify the work (max 5), with a role for each.
  ```
- **Trade-offs**:
  - (+) Quotes are guaranteed verbatim and chosen from a defensible rule; time, app and durations are exact; cheap and reproducible; the closest to the one study that measured factual QA (Activity Frames), though that study did not include an LLM narrative layer.
  - (+) Boundaries are inspectable and testable without a model.
  - (-) Rule-based sessions split or merge badly when the real task crosses apps (IDE + terminal + browser for one goal): Dayflow and FOCAL both exist because grouping by app is wrong ("Group by GOAL not app"). A hybrid would need an LLM boundary pass.
  - (-) Needs reliable accessibility/OCR text and window/URL metadata from the laptop client; if the client only sends screenshots, the candidate quote list does not exist and a vision model must do the extraction (like Dayflow/GUM).
  - (-) Rules (length 30 to 300, typed text first) were tuned by Screenpipe for its own purposes; Vitality would need to tune them.

#### Choices that are independent of the design

- **Who sees what**: the three designs differ in what is passed as "context"; the previous-15-observations approach is not used by any of the systems studied as the primary context.
- **Quote count and role tags**: none of the systems found publish an optimal quote count. Screenpipe caps at 20 per query range; MIRIX has no quote field. Starting with a cap per record (e.g. 3 to 8) and role labels is a design choice to test, not a sourced figure.
- **Keep raw notes**: Dayflow and Codex both keep the lower layer and re-derive the upper one; MIRIX overwrites in place. Whether to keep per-interaction notes is a storage/auditability trade-off.
- **Evaluation**: a cheap check suggested by the literature is question-answering over the records (Activity Frames, MIRIX): write 20 to 30 questions about what you actually did on a few days, answer them from (a) the current observations and (b) each candidate record format, and compare. This has not been validated for Vitality and the sample is yours to define.

### 9.4 What I could not verify

- Chronicle's actual capture/summarisation prompts and cadence (closed source; official docs now describe "Computer History" without screenshots).
- Recall's internal database schema in current Windows builds and whether it generates any text summaries (official docs describe none).
- Rewind/Limitless internals (secondary sources only; product discontinued).
- Whether Screenpipe's `workflow-*` pipes define "episodes" with a documented boundary rule (I read only `workflow-activity/pipe.md` and the file listing).
- The model/default settings of GUM's repo versus the paper, and the quality of Dayflow's cards (no published evaluation).
- All paper numbers above come from a tool-generated summary of the paper text; re-check against the PDFs before citing.
