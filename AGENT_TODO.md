# Agent Work Checklist

> Purpose: track the remaining Agent-side work and keep each change reviewable as
> an independent commit. Update this file after every step, but do not combine
> unrelated implementation work into a checklist-only commit.

## 0. Scope And Guardrails

- [x] Read `../plan.md` as the desired prompt/runtime specification.
- [x] Read `../kGroup实习生笔试题-候选人版-2026-08-10.md` as the source-of-truth assignment.
- [x] Review the repository structure and existing Git history.
- [x] Confirm the existing 10 commits are all authored by the same contributor.
- [x] Leave the credential file outside the repository unread and untracked.
- [ ] Record both contributors and ownership in `COLLAB.md` before delivery.

The assignment wins when it conflicts with `plan.md`. In particular,
`schedule_followup` means "mark for later follow-up, do not reply in this turn".

### Agreed Agent Architecture

These decisions are fixed for A1-A7 unless both contributors revise this file
before implementation:

- Keep the current direct Google Gemini SDK integration and adapter boundary.
  Do not introduce LangChain for this two-call classification pipeline.
- Classify in two ordered, independently validated LLM calls:
  `IntentClassifier -> {intent}`, then
  `DissatisfactionClassifier -> {dissatisfied}`.
- The dissatisfaction call receives the same untrusted customer message (and,
  after A4, the same bounded customer history), but it must not receive or infer
  from the intent result. The two labels remain semantically orthogonal.
- Combine the two validated outputs into the internal `Estimation` only after
  both calls succeed. If either call fails, raise `LLMError` and fail closed
  before any state transition, policy decision, reply generation, or action.
- Keep classifier and reply generator as separate responsibilities. A successful
  two-call classification is followed by a separate reply-generation call only
  when the deterministic pipeline selects `reply`.
- Complete one checklist step per working session and one implementation concern
  per commit. Do not start a later step early because a nearby file is open.

### Frozen Security Core

Do not modify these files during the Agent-side implementation unless both
contributors explicitly agree to a separate security review:

- `src/kapibala/executor.py`
- `src/kapibala/rate_limiter.py`
- `src/kapibala/state_machine.py`
- `src/kapibala/output_guard.py`
- `tests/test_executor.py`
- `tests/test_rate_limiter.py`
- `tests/test_state_machine.py`
- `tests/test_output_guard.py`
- `scripts/attack_test.py`
- `docs/attack_report.md`

The action allowlist, terminal-state silence, shared anomaly counter, rolling
60-second limiter, canary, and credential checks must retain their current
behavior. Before every commit, run:

```bash
git diff --name-only
git diff --check
```

If a frozen file appears in `git diff --name-only`, stop and split/review the
change before committing.

## 1. Current Baseline

- [x] M0-M5, R1, and R2 are present in Git history.
- [x] Offline non-Gemini suite: `72 passed` on 2026-08-20.
- [x] Full `88`-test suite rerun on 2026-08-20 in a temporary project
      environment with `google-genai` and `python-dotenv` installed.
- [ ] Fake CLI smoke test rerun after installing project dependencies.
- [ ] Real Gemini smoke/evaluation rerun only when explicitly approved, because
      it consumes the supplied API quota.

Current local verification commands:

```bash
PYTHONPATH=src pytest -q \
  --ignore=tests/test_gemini_adapter.py \
  --ignore=tests/test_gemini_reply.py
python3 -m pip install -e ".[dev]"
pytest -q
```

The first command currently passes. The full suite and CLI currently cannot be
run in the active shell because the declared runtime dependencies are not
installed there; this is an environment prerequisite, not a confirmed code
failure.

## 2. Remaining Gaps

### Required By `plan.md` But Missing

- [ ] Align the Classifier contract with the exact two-field schema in
      `plan.md`: `intent` and `dissatisfied`. The current
      `followup_requested`, `confidence`, and `reason` fields are project
      extensions and must not silently remain part of the LLM control contract.
- [ ] Validate `customer_id`, blank messages, and maximum message length before
      calling either LLM.
- [ ] Keep a bounded recent conversation history per customer, with tests proving
      histories cannot cross customer IDs.
- [ ] Supply recent history plus explicit company/product context to the reply
      generator instead of relying on a generic company description.
- [ ] Detect an explicit request for a human in an independent business guard
      before ordinary intent classification. Do not add an action or control
      field to the Classifier output schema.
- [ ] Add failure coverage for reply-generation and Agent orchestration failures.
- [ ] Add three recorded end-to-end sales conversations: normal interest,
      explicit rejection, and consecutive bad signals.

### Assignment Mismatch To Fix

- [ ] Current code lets an LLM-only `followup_requested` boolean create both
      `reply` and `schedule_followup`. This conflicts with both specifications:
      the assignment says no reply in that turn, and `plan.md` requires an
      independently extracted and validated time.
- [ ] Remove automatic follow-up scheduling from the intent classifier path. For
      this time-boxed demo, prefer a documented trusted-operator scheduling
      command over pretending the fixed one-hour delay was requested by the
      customer. A validated natural-language time extractor is a separate,
      optional feature and must not be improvised with a loose keyword list.
- [ ] If the queue remains part of the demo, ensure a failed/rate-limited
      follow-up is not silently discarded; preserve it or explicitly cancel it
      with an auditable reason.

### Documented But Frozen For This Workstream

These are specification-ordering/security concerns. Record them for the owners
of the frozen core; do not fold them into an Agent feature commit:

- [ ] `ScreeningAgent` currently generates a reply before the executor checks
      the rate limit, while `plan.md` says a blocked reply must not call the reply
      generator.
- [ ] `plan.md` says reply-generator/output-guard failures fail closed; the
      current Gemini generator falls back to a template. Decide and document one
      contract in a separate security review.
- [ ] The send timestamp is recorded before the sink call returns. Review failure
      behavior separately if the demo sink is replaced by a real transport.

## 3. Commit Plan

All Agent commits below should be authored by the contributor who did not author
the existing M0-R2 history. Each feature commit includes its tests so the history
shows implementation and verification together.

### A1 - Classifier Contract Alignment

Suggested commit:

```text
refactor(agent): split intent and dissatisfaction classification
```

- [x] Keep `LLMAdapter.estimate(...) -> Estimation` as the business-facing
      boundary so `ScreeningAgent` and the frozen core do not depend on an SDK.
- [x] Implement ordered Call 1 with an exact structured schema containing only
      `intent`.
- [x] Implement ordered Call 2 with an exact structured schema containing only
      `dissatisfied`.
- [x] Do not include the Call 1 result in the Call 2 prompt, request object, or
      examples. In A1 both calls use the current message only; bounded history is
      added later in A4.
- [x] Construct the internal `Estimation` only after both response schemas pass.
- [x] If Call 1 fails, do not call Call 2. If Call 2 fails, discard Call 1 and
      propagate `LLMError`; in both cases the Agent must return fail-closed with
      no state/action/reply side effect.
- [x] Keep the five required intent labels and their deterministic mappings.
- [x] Remove Agent decisions that depend on `followup_requested`, `confidence`,
      or free-form `reason`.
- [x] Keep evaluation focused on intent accuracy and dissatisfied macro F1.
- [x] Keep FakeAdapter deterministic for offline tests; it may return the final
      combined `Estimation` without simulating network calls.
- [x] Test call order/count, exact schemas, Call 1 failure, Call 2 failure,
      invalid output, and that the dissatisfaction request contains no intent
      result.
- [x] Update Fake/Gemini adapter, policy, evaluation, and affected pipeline tests
      in the same commit without changing frozen security tests.
- [x] Run the full offline suite and record the result below.
- Commit: `__________`
- Test result: `2026-08-20: focused 43 passed; full offline 88 passed`

### A2 - Runtime Input And Conversation Context

Suggested commit:

```text
feat(agent): add validated runtime input and isolated conversation history
```

- [ ] Add an Agent-side runtime input model/config without changing the frozen
      security core.
- [ ] Reject missing/blank customer IDs, blank messages, and over-limit messages
      before classifier invocation or implicit customer-state creation.
- [ ] Add a small in-memory `ConversationStore` with an injectable maximum number
      of turns and explicit customer isolation.
- [ ] Store accepted active-session inbound messages and only replies confirmed
      as successfully sent; never store a rate-limited draft as an assistant
      message.
- [ ] Define and test ordering/truncation behavior, including fail-closed,
      rate-limited, escalated, and closed outcomes.
- [ ] Do not inject history into Gemini/Fake prompts in A2. A2 establishes and
      tests the runtime/history data contract; A4 performs prompt propagation.
- [ ] Test no-LLM behavior for invalid input, bounded history, ordering, and
      cross-customer isolation.
- [ ] Run the full offline suite and record the result below.
- Commit: `__________`
- Test result: `__________`

### A3 - Explicit Human Request Guard

Suggested commit:

```text
feat(agent): add a pre-classifier human handoff guard
```

- [ ] Implement an independent, narrow business-intent detector that runs after
      input/terminal guards and before the ordinary intent classifier.
- [ ] Normalize supported explicit Chinese and English handoff expressions and
      document the detector's semantic boundary; do not present it as a general
      prompt-injection defense.
- [ ] Map a positive result only to the existing `escalate_to_human` enum.
- [ ] Assert that the ordinary classifier and reply generator are not called for
      a recognized explicit human request.
- [ ] Prove with tests that later customer text cannot reactivate the session.
- [ ] Add varied Chinese and English positive/negative handoff examples.
- [ ] Run the full offline suite and record the result below.
- Commit: `__________`
- Test result: `__________`

### A4 - Context-Aware Classification And Reply Drafts

Suggested commit:

```text
feat(agent): use bounded business and conversation context in both LLM calls
```

- [ ] Introduce explicit company/product context with conservative defaults.
- [ ] Extend adapter/generator request interfaces to accept structured context;
      do not concatenate trusted and untrusted content without clear roles or
      delimiters.
- [ ] Load history from the A2 `ConversationStore` and pass only the current
      customer's bounded history to both classifier calls.
- [ ] Pass company/product context, bounded history, current message, intent,
      dissatisfaction, and reply kind to the reply generator.
- [ ] Keep the two classification calls distinct from the later reply-generation
      call. A reply path therefore uses three LLM requests; a non-reply path uses
      only the two classification requests.
- [ ] Confirm the dissatisfaction prompt still receives no intent result after
      context propagation.
- [ ] Test context propagation, truncation, customer isolation, and fallback
      behavior.
- [ ] Add a test that missing product facts do not become fabricated claims.
- [ ] Run the full offline suite and record the result below.
- Commit: `__________`
- Test result: `__________`

### A5 - Conservative Follow-Up Contract

Suggested commit:

```text
fix(agent): make follow-up scheduling trusted and explicit
```

- [ ] Remove LLM-only automatic scheduling from the intent mapping.
- [ ] Add a trusted operator command that creates a validated follow-up marker or
      due time through the existing `schedule_followup` action.
- [ ] Ensure scheduling itself sends no reply and consumes no send quota.
- [ ] Preserve failed/rate-limited follow-ups for retry, or record an explicit
      terminal cancellation decision.
- [ ] Test due/not-due, retry, escalated, closed, and reactivated cases without
      modifying the frozen executor/state-machine tests.
- [ ] Document automatic natural-language scheduling as deliberately cut unless
      a validated time extractor is implemented later.
- [ ] Run the full offline suite and record the result below.
- Commit: `__________`
- Test result: `__________`

### A6 - Usable And Inspectable CLI Demo

Suggested commit:

```text
feat(cli): expose agent history follow-ups and audit state in the demo
```

- [ ] Keep terminal UI as the chosen minimal simulated conversation channel.
- [ ] Add read-only commands to inspect bounded history, queued follow-ups, and
      customer audit events.
- [ ] Make invalid-input and no-op outcomes visible to the operator.
- [ ] Keep fake mode deterministic and usable without an API key.
- [ ] Add CLI tests for all new commands and important error messages.
- [ ] Run the full offline suite and record the result below.
- Commit: `__________`
- Test result: `__________`

### A7 - Acceptance Evidence And Delivery Docs

Suggested commit:

```text
docs: add collaboration record and agent acceptance evidence
```

- [ ] Add `COLLAB.md`: both contributors, ownership, disagreements, resolutions,
      and the commit ranges each person can explain in the interview.
- [ ] Record three required end-to-end conversations and their classifier output,
      state transition, selected action, and execution result.
- [ ] Explain why direct SDK integration was chosen over an Agent framework.
- [ ] Document every deliberate cut and known limitation honestly.
- [ ] Update README startup commands from a clean environment.
- [ ] Update actual time spent; do not rewrite old commit authorship.
- [ ] Run `pytest -q` and the fake CLI smoke flow from the documented commands.
- [ ] With approval, run one real Gemini smoke flow and record model/date/result
      without recording the API key or raw credentials.
- Commit: `__________`
- Test result: `__________`

## 4. Per-Commit Procedure

Use this procedure for A1-A7. Do not create empty commits to manufacture a
two-person history.

- [ ] Start by reading this entire checklist, `../plan.md`, the kGroup assignment,
      current `git log`, `git status`, and all files relevant to the current step.
- [ ] Confirm every earlier step is committed and its focused/full test result is
      recorded. Do not build A2 on an unreviewed A1 worktree.
- [ ] Confirm author identity: `git config user.name` and `git config user.email`.
- [ ] Confirm clean starting point: `git status --short`.
- [ ] Implement only the current step.
- [ ] Add focused tests in the same commit.
- [ ] Run focused tests, then the complete offline/full suite as available.
- [ ] Run `git diff --check`.
- [ ] Confirm no frozen security file changed with `git diff --name-only`.
- [ ] Review the staged patch: `git diff --cached`.
- [ ] Commit with the suggested message (adjust wording, not scope).
- [ ] Write the commit hash and test result into this checklist in the next
      implementation commit, avoiding checklist-only noise after every step.

### New Session Instruction Template

Replace `<STEP>` with exactly one of A1-A7:

```text
Read AGENT_TODO.md, ../plan.md, the kGroup assignment, current git log/status,
and the source/tests relevant to <STEP>. Complete <STEP> only. Do not implement
later steps and do not modify any Frozen Security Core file. Follow the fixed
Agent architecture in AGENT_TODO.md, add focused tests in the same change, run
focused and complete available tests, run git diff --check, and report changed
files plus a suggested commit message. Do not commit until I review the diff.
```

After reviewing the diff, explicitly request the commit in a second message.
Never ask a new session to complete `A1/A2/...` as a combined task.

## 5. Final Gate

- [ ] Git history contains substantive, explainable commits from both people.
- [ ] `git status --short` is clean.
- [ ] Full test suite passes in the documented environment.
- [ ] Fake CLI demo works without a key.
- [ ] Real LLM intent classification is demonstrated once with approval.
- [ ] No `.env`, API key, credential text, cache, or security-lab artifact is
      tracked by Git.
- [ ] Assignment-required actions and five intents are unchanged.
- [ ] Four hard constraints remain enforced by the frozen core.
- [ ] Follow-up behavior matches "mark later, no reply this turn".
- [ ] README, `COLLAB.md`, acceptance evidence, and known limitations agree with
      the actual code and test results.
