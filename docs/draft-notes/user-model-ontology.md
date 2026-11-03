# User model ontology: findings and baseline

Draft note, 2026-11-03. Decision: use Tempo's striving hierarchy as the baseline structure for the
user model, knowing it leaves out a few dimensions.

## Decision

- **Baseline:** Tempo's hierarchy (operations → actions → activities → strivings), possibly slimmed
  to observations → activities → strivings once real data shows whether a separate action level
  earns its keep.
- **Everything above observations is a belief.** Strivings, activities, preferences and facts are
  all propositions with confidence, decay, reasoning and links to the observations behind them.
  One format, one store (Markdown files, `[[links]]`).
- **The hierarchy is an inductive bias in the proposer, not a storage schema.** The proposer is
  told to build and maintain it bottom-up; propositions of kind `activity` and `striving` carry
  required links, other kinds don't.
- **Known gaps, to fill as free propositions for now:** preferences, interests, and concrete
  obligations (deadlines, duties). Tempo only partly covers obligations, through its
  `aspiration_vs_obligation` field (ideal / ought / mixed) on strivings; a dated duty like a co-op
  assignment is not a striving. Let the competency questions show which gaps matter.
- **Observations stay the permanent evidence.** Everything above them can be rebuilt with a
  better proposer prompt, so the ontology is a reversible choice.

## Tempo (UIST '26)

"What Are You Really Trying to Do?": Co-Creating Life Goals from Everyday Computer Use. Sapkota,
Jörke, Sabbagh, Shaikh, Wang, Landay (Stanford). arXiv 2605.00497; code
github.com/StanfordHCI/striving-cocreation. Presented 2026-11-03, session "User-Aware Agents".

- **Levels.** Operations: atomic, purely descriptive ("make no claim about why"). Actions:
  contiguous operations sharing one intent; boundaries at intent shifts, not app switches.
  Activities: recurring patterns of actions with one motive, across apps and days. Strivings
  (`goal` in the code): ongoing pursuits that organize several activities.
- **A graph, not a tree:** one action can serve several activities, one activity several strivings.
- **Striving tests (from Emmons):** recurs with no endpoint; shows in behaviour; abstracts over
  several activities.
- **Striving fields (`GoalItem`):** text ("X is …"), activity_ids, reasoning, confidence 1–10,
  evidence summary, needs (12 tags from SDT, Dweck, Murray, Higgins), orientation
  (approach/avoidance/mixed), aspiration_vs_obligation, autonomy, identity_link, feared_self,
  people, domains. Relations between strivings: conflict, facilitation, instrumental.
- **Pipeline:** chained single LLM calls (Gemini 3 Flash), not an agent. Operations from screen
  transcriptions; operations buffered until a batch or idle, then segmented into actions; activities
  by propose-then-reconcile (match, revise, merge, create); strivings by synthesise-then-self-refine.
  12 onboarding questions give user context. User edits become constraints.
- **Results (N=14, one week).** Hierarchy did not improve individual striving quality
  (β=0.049, p=.68) but did improve set-level representativeness (β=0.721, 95% CI [0.10, 1.34]),
  most on "motive" (0.79 vs 0.07): without it, strivings describe what you do, not why. User
  context improved individual quality (β=0.29, p<.05, mostly alignment) but caused confident
  misattributions when over-weighted. The hierarchy view also made editing far more transparent
  than raw screenshots. Errors: unrelated activities merged on surface features; work over-represented.

## Other findings

- **GUM:** no ontology on purpose; free-form propositions with confidence and decay. Its theory is
  common ground and memory decay, not psychology of the person. Fixed batches (5–50 observations).
  Interventions live in Gumbo, per new proposition, gated by expected value, max one per minute.
- **Emmons (1986, JPSP 51:1058):** defines strivings and elicits them by self-report ("I typically
  try to…", rated on importance, effort, ambivalence, success). Positive affect tracks valuing and
  fulfilling strivings; negative affect tracks ambivalence (r=.65) and conflict. Says nothing about
  inferring strivings from observation. The elicitation method could seed a user-context file.
- **How Tempo's inference works comes from LLM concept induction,** not psychology: LLooM (Lam et
  al., CHI 2024), goal-driven explainable clustering (Wang, Shang, Zhong 2023), AI Chains (Wu et al.
  2022). LLooM is the next read for the proposer's design.
- **Goal hierarchy and taxonomy literature**, if more structure is needed later: Austin & Vancouver
  (1996) review; Carver & Scheier's be/do hierarchy; Vallacher & Wegner's action identification;
  Kruglanski's goal systems (network, one means many goals); content taxonomies by Ford & Nichols
  (1987), Chulef, Read & Walsh (2001), Talevich et al. (2017, open access).

## Architecture this fits into

```
interactions → observer (Claude Code, batches) → rich observations (.md, episodic)
            → proposer agent → propositions incl. the striving hierarchy (.md, semantic)
            → opportunities → persistent agent → user
```

The proposer maintains beliefs and does not interrupt; interrupting belongs to the persistent agent.

## Next

1. Rich observations: new prompt and format; compare on a fixed set of ~20 interactions.
2. ~10 competency questions from Yanis.
3. A short user-context file written by Yanis (Tempo's onboarding, Emmons' elicitation).
4. First proposer with the baseline hierarchy; check against the questions; rebuild.
