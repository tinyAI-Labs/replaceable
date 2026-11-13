---
name: replaceable
description: Decide whether an agent or child loop does deterministic work (replace with a fixed rule) or probabilistic work (LLM justified), by fitting it as a Lean 4 function and replaying logged cases it has never seen, and report the tokens a replacement would save. Use when auditing agent loops for LLM necessity, from a log of typed inputs and outputs.
---
# Replaceable
Can the loop be written as a total Lean function of typed inputs that reproduces its logged answers on cases the rule never saw? One command fits a rule on the early half of the log, replays it once on the held-back half and computes the verdict. A weak rule ends INCONCLUSIVE, never DETERMINISTIC.

Needs `python3` 3.9+ (standard library only) and `lean` (Lean 4) on PATH. `scripts/` is the directory next to this file.

## Procedure
1. In the directory with `schema.json`, `Types.lean` and `logs/early.jsonl`, run `python3 scripts/run.py`. With only a raw log (JSON lines with `typed_input` and `output`), first run `python3 scripts/prepare.py raw.jsonl`.
2. A `VERDICT:` line means you are done: `verdict.json` is written. Report the verdict, the scope, the `WHY:` text and the `TOKENS:` line as printed. For a deterministic verdict add that a person must confirm that `Rule.lean` matches the written policy.
3. Otherwise run exactly the command after `NEXT:`. Its one question is the **scope**: look at one log line. If the typed fields were read out of source material in it (a document, a message, a tool result), use `--scope decision_step_only` (only the decision is tested; the extraction stays probabilistic). If they are the loop's direct input, use `--scope full`.

Nothing else is a step. Do not analyse the log yourself and do not edit `Rule.lean`: a weak rule goes to the exam as it is, and INCONCLUSIVE is then the correct result. If its `WHY:` says the agreement still rises with more cases, tell the owner that a longer log may settle the loop.

**Optional, first run only.** `run.py` already tries products and differences of two numbers and the parts of a date. If a field's name or unit says the policy computes something else from it (a remainder or digit group of an identifier, such as `x.account_no % 10`), offer it: `python3 scripts/run.py --derive "x.account_no % 10"` (repeatable). It is kept only if cross-validation on the early half improves. After the exam it is too late.

## Tokens
If the log lines carry a token count per loop run (`tokens`, or `usage` / `input_tokens` + `output_tokens` as the SDKs log them), the `TOKENS:` line and the `tokens` block of `verdict.json` give the tokens per run, the total over the log, the share of runs the rule would take over (all for DETERMINISTIC and CONSTANT, the part outside the fallback region for DETERMINISTIC_CORE_FALLBACK, none otherwise) and the tokens saved, with the arithmetic. Without a count they say `unknown`: never estimate one. Token counts never influence the rule or the verdict, and for `decision_step_only` the saving is an upper bound.

## Hard rules
- Never open, copy or fit on `logs/heldout.jsonl`; only the replay reads it, once.
- Never change `Rule.lean` after the replay.
- Never write `verdict.json` by hand. To disagree: `python3 scripts/verdict.py --note "..."`.
- Low agreement is never PROBABILISTIC: it means no rule was found. Only identical inputs with different outputs show a probabilistic loop.
- Take every number from script output.

## Verdicts (`verdict.py`)
| Evidence | Verdict |
|---|---|
| Output has no fixed set of values (free text) | OUT_OF_SCOPE |
| Under 150 logged cases | INCONCLUSIVE |
| Identical inputs, different outputs: over 5% of the early cases whose input repeats, for 2 or more inputs | PROBABILISTIC |
| Replay: all outputs equal, L >= 0.95 | CONSTANT |
| Replay: L >= 0.95 and 1 - L at most half of 1 - baseline | DETERMINISTIC |
| Replay: L >= 0.80, a declared `fallback` region, 60+ held-back cases outside it with bound >= 0.95 there | DETERMINISTIC_CORE_FALLBACK |
| Anything else | INCONCLUSIVE |

L is the exact one-sided 95% lower bound on held-out agreement; baseline is the share of the most common held-out output; `fallback` is the region the rule hands back to the LLM.

## For the host
The held-back half must stay unseen. The scripts refuse it as a fitting log and allow one replay, but cannot stop an agent that reads files. A host that can should keep `logs/heldout.jsonl` out of the agent's reach: `run.py` then leaves `work/replay_request.json` and the host answers it with `python3 scripts/host_replay.py --heldout <file> --workdir <agent directory>`.

`selftest.py` verifies an installation on generated loops with known answers. `audit.py` alone shows the fit without taking the exam.
