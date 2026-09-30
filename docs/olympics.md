# Performance Olympics

After each milestone, the project runs a two-wave agent competition to find the largest remaining latency improvement in the rungs just built. Decided 2026-09-30.

Agents tend to propose quick, local hacks. This project is open to long, expensive rewrites when the payoff is large, so the competition is set up so that ambition is rewarded and the size of the win is decided by measurement, not by the pitch.

## When it runs

At the end of each milestone, before its gate review:

| Milestone | Rungs in play | Measured with |
| --- | --- | --- |
| Phase A (R1–R3 built, harness working) | R2, R3 (R4 if built) | The Phase A software harness |
| Phase B (R5 built, minimal rig working) | R2–R5 | The minimal photodiode rig, plus the software harness for web rungs |
| Phase C milestones | R2–R6 | The full rig |

R1 is never a target. It stays the typical-team baseline.

## Wave 1: ideas

- **Who:** several agents, each working alone and seeing none of the others' proposals, with read access to the rungs, the latest measurement report (percentiles plus the per-stage breakdown) and traces. Each gets a different starting lens so they don't converge on the same idea. Examples: input path, main-thread work, rendering and layout, search algorithm and data structures, scheduling across threads, presentation and frame pacing, "rewrite the rung from scratch".
- **What they submit:** up to three proposals each. No limit on implementation complexity: a months-long rewrite is as welcome as a one-line fix. Each proposal states:
  - the change, and which layer's latency it removes;
  - the predicted effect on the headline metric (p95 and p50, in ms), with the reasoning and evidence from the traces;
  - the estimated effort (agent-hours) and risk;
  - which parity checks it could endanger, and how it keeps them passing;
  - the smallest experiment that would show whether it is right.
- **How they are told they will be scored:** by the **measured** improvement of their proposal once built, not by the prediction. A proposal whose build shows little gain scores little however well argued. Effort is recorded, but it does not reduce the score, so agents have no reason to avoid expensive ideas.
- **Selection:** a human (with an agent's help to remove duplicates) picks what to build. The default is to build the top proposals by predicted gain regardless of cost, plus any cheap ones that are nearly free to try. Merging proposals that attack the same layer is allowed.

## Wave 2: builds

- **Who:** one builder agent per selected proposal, each in its own git worktree and branch, so builds don't interfere. Long-running builds are expected; the builder works until the proposal is implemented as specified or shown to be infeasible, and says which.
- **Rules**, the same guardrails as the spec's agent optimization loop:
  - Builders may change only their rung's directory. The harness, parity suite, dataset generator and reference ranker are off limits.
  - The build must pass the full parity suite available at that phase (correct results against the reference ranker, keyboard, text editing, marker honesty and the visual diff).
  - No detecting the test environment, dataset or seed; final numbers use held-out data the builders never saw.
  - Changes to input handling, frame timing or present modes get human review.
- **Measurement:** the harness measures each build against the current rung, interleaved, with the phase's noise band. A gain counts only if it clears the noise band, and from Phase B also on a second machine.

## Results and scoring

- For each proposal, the record keeps: predicted gain, measured gain (p50, p95, p99 with confidence intervals), actual effort, lines of code, parity result, and whether the change was merged.
- The **winner** is the proposal with the largest measured p95 improvement that passes parity. A proposal the agent predicted to be small but that measured large still wins.
- **Prediction accuracy** is tracked per agent lens as a secondary result. It shows which kinds of reasoning about latency hold up.
- Winning changes are merged into the rung before the gate review, so the gate judges the best version found.
- The spec already treats effort as part of the answer, so both measured gain and effort are reported for every proposal. That shows what a large win cost to get.

## Agent budget and fairness

The spec gives each of R2 to R5 the same agent budget. Olympics effort is counted against that budget and reported per rung, so a rung that benefited from an expensive rewrite shows it.

## Prompt outline (Wave 1)

> You are competing with other agents to find the largest remaining latency improvement in rung {R}. Your score is the **measured** improvement in p95 keystroke-to-screen latency once your proposal is built by another agent, not your predicted improvement. There is **no limit on implementation complexity**: rewrites, new architectures, multi-week efforts and new languages are all allowed if the gain justifies them. Effort is recorded but does not reduce your score. Proposals must keep the rung passing the parity suite: {checks}. Your starting lens is {lens}, but you may propose anything. Here is the latest measurement report and traces: {report}. Submit up to three proposals, each with the change, the layer it attacks, the predicted p50/p95 gain with evidence, effort, parity risks, and the smallest experiment that would test it.
