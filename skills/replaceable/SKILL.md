---
name: replaceable
description: Decide whether an agent or child loop does deterministic work (replace with a fixed rule) or probabilistic work (LLM justified), by fitting it as a Lean 4 function and replaying logged cases it has never seen, and report the tokens a replacement would save. Use when auditing agent loops for LLM necessity, from a log of typed inputs and outputs.
---
# Replaceable
Can the loop be written as a total Lean function of typed inputs that reproduces its logged answers on cases the rule never saw? One command fits a rule on the early half of the log, replays it once on the held-back half and computes the verdict. A weak rule ends INCONCLUSIVE, never DETERMINISTIC.

Needs `python3` 3.12+ (standard library only) and `lean` (Lean 4) on PATH. The scripts are in `${CLAUDE_SKILL_DIR}/scripts`, the `scripts/` directory next to this file (where a command below still shows a `${...}` placeholder instead of a path, write this file's directory there). Run them from the directory that holds the log, not from here; every `NEXT:` line prints the full command.

## Procedure
1. In the directory with `schema.json`, `Types.lean` and `logs/early.jsonl`, run `python3 ${CLAUDE_SKILL_DIR}/scripts/run.py`. With only a raw log (JSON lines with `typed_input` and `output`, and `correct` true/false per run if the loop's runs have feedback: `prepare.py` then sets aside the runs before the loop held 95% accuracy and the `Answer:` says from which run it checked), first run `python3 ${CLAUDE_SKILL_DIR}/scripts/prepare.py raw.jsonl`.
2. An `Answer:` line means you are done: `verdict.json`, `report.md` and `report.html` are written. Reply with the compact block run.py printed (its header, the `Answer:` line, the `Tokens:` line and the line naming the report), each as printed and nothing else, after every verdict, in every conversation. If the user wants the full picture, show them `report.md` as it is and name `report.html`, the same page for a browser. Print the rest only if the user asks why or how: run `python3 ${CLAUDE_SKILL_DIR}/scripts/run.py --why` in the same directory (it resumes; nothing is replayed again) and report its `VERDICT:`, `WHY:` and `TOKENS:` lines. If the user says what they pay per million tokens, run `run.py --usd-per-mtok <price>` the same way so the `Tokens:` line quotes the cost; never guess a price.
3. Otherwise run exactly the command after `NEXT:`. Its one question is the **scope**: look at one log line. If the typed fields were read out of source material in it (a document, a message, a tool result), use `--scope decision_step_only` (only the decision is tested; the extraction stays probabilistic). If they are the loop's direct input, use `--scope full`.

Nothing else is a step. Do not analyse the log yourself and do not edit `Rule.lean`: `run.py` writes the fitted rule and examines exactly that rule, so an edit made before the exam is overwritten and one made after it turns the verdict INCONCLUSIVE. A weak rule goes to the exam as it is, and INCONCLUSIVE is then the correct result.

**Optional, first run only.** `run.py` already tries products and differences of two numbers and the parts of a date. If a field's name or unit says the policy computes something else from it (a remainder or digit group of an identifier, such as `x.account_no % 10`), offer it: `python3 ${CLAUDE_SKILL_DIR}/scripts/run.py --derive "x.account_no % 10"` (repeatable). It is kept only if cross-validation on the early half improves. After the exam it is too late.

## Tokens
The `Tokens:` line is always printed; the arithmetic is printed only with `run.py --why`. If the log lines carry a token count per loop run (`tokens`, or `usage` / `input_tokens` + `output_tokens` as the SDKs log them), the `TOKENS:` line and the `tokens` block of `verdict.json` give the tokens per run, the total over the log, the share of runs the rule would take over (all for DETERMINISTIC and CONSTANT, none otherwise) and the tokens saved, with the arithmetic. Without a count they say `unknown`: never estimate one. Token counts never influence the rule or the verdict, and for `decision_step_only` the saving is an upper bound.

## Hard rules
- Never open, copy or fit on `logs/heldout.jsonl`; only the replay reads it, once.
- Never change `Rule.lean`, before or after the replay: it is the solver's rule, not yours.
- Never write `verdict.json` by hand. To disagree: `python3 ${CLAUDE_SKILL_DIR}/scripts/verdict.py --note "..."`.
- Low agreement is never PROBABILISTIC: it means no rule was found. Only identical inputs with different outputs show a probabilistic loop.
- Take every number from script output.

## Verdicts (`verdict.py`)
| Evidence | Verdict |
|---|---|
| Output has no fixed set of values (free text) | OUT_OF_SCOPE |
| The log carries `correct` and the loop's feedback changed recently (a step in the last 300 runs, or a slope across the steady stretch): no exam is read | INCONCLUSIVE |
| Under 150 logged cases | INCONCLUSIVE |
| Identical inputs, different outputs: over 5% of the early cases whose input repeats, for 2 or more inputs | PROBABILISTIC |
| Replay: all outputs equal, L >= 0.95 | CONSTANT |
| Replay: L >= 0.95 and 1 - L at most half of 1 - baseline | DETERMINISTIC |
| Otherwise, if the optional learned model (`scripts/witness/`, trained on the early half, discarded) reproduced the held-back cases with a 99% bound >= 0.95 and 1 - bound at most half of 1 - baseline | DETERMINISTIC_LEARNED |
| Anything else | INCONCLUSIVE |

`DETERMINISTIC_LEARNED` means the typed fields decide the output and no readable rule was found: report it as that, and never offer the model as a replacement (it is gone after the exam). L is the exact one-sided 95% lower bound on held-out agreement; baseline is the share of the most common held-out output; `fallback` is the region the rule hands back to the LLM.

## For the host
The held-back half must stay unseen. The scripts refuse it as a fitting log and allow one replay, but cannot stop an agent that reads files. A host that can should keep `logs/heldout.jsonl` out of the agent's reach: `run.py` then leaves `work/replay_request.json` and the host answers it with `python3 ${CLAUDE_SKILL_DIR}/scripts/host_replay.py --heldout <file> --workdir <agent directory>`.

`selftest.py` verifies an installation on generated loops with known answers. `audit.py` alone shows the fit without taking the exam.
