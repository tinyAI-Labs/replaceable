# replaceable

Is this agent loop doing work a fixed rule could do? `replaceable` is an agent skill (a `SKILL.md` plus standard-library Python scripts) that answers
from the loop's own log: it fits a small, readable rule to the earlier half of the logged cases, writes it as a total Lean 4 function, replays it once
on the later half it has never seen, and returns one of six verdicts with the evidence. If the log says what each run cost, it also says how many
tokens the replacement would save.

| Verdict | Meaning |
|---|---|
| `DETERMINISTIC` | a rule reproduces the held-back cases (95% lower bound on agreement >= 0.95, clearly better than a constant) |
| `DETERMINISTIC_CORE_FALLBACK` | a rule covers most cases; a declared region is handed back to the LLM |
| `CONSTANT` | every logged case has the same output |
| `PROBABILISTIC` | identical inputs received different outputs |
| `INCONCLUSIVE` | not enough evidence (too few cases, or no rule found); never read as "probabilistic" |
| `OUT_OF_SCOPE` | the output is free text, not a typed decision |

## Install
Needs Python 3.12+ (no packages) and Lean 4 on `PATH` (`curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y`; the tested version is in
`lean-toolchain`). In Claude Code:

    /plugin marketplace add tinyAI-Labs/replaceable
    /plugin install replaceable@tinyai-labs

For another agent, copy `skills/replaceable/` to where it keeps skills. Then check the install:

    python3 skills/replaceable/scripts/selftest.py     # nine generated loops with known answers, about a minute

## Use
Give the agent a log and ask whether the loop is replaceable, or run the two commands yourself:

    python3 skills/replaceable/scripts/prepare.py raw.jsonl   # types the log, splits it by time into logs/early.jsonl and logs/heldout.jsonl
    python3 skills/replaceable/scripts/run.py                 # audit, one-shot replay, verdict: ends with VERDICT / WHY / TOKENS and verdict.json

`raw.jsonl` has one logged run per line: `{"typed_input": {...}, "output": ..., "ts": ..., "tokens": 1840}`. `typed_input` holds numbers, booleans,
dates and categories; `ts`, `case_id` and the token count are optional. At least 150 runs are needed. `run.py` asks at most one question (the scope:
were the typed fields extracted from documents by the loop itself?) and prints the exact command to answer it.

## Tokens and savings
Log a token count per run as `tokens` (the run's total), or as the SDKs report it: `usage.input_tokens` + `usage.output_tokens` (cache tokens are
added), `prompt_tokens` + `completion_tokens`, or `total_tokens`. The verdict then carries a `tokens` block and a `TOKENS:` line: tokens per run,
the total over the log, the share of runs the rule would take over (all for `DETERMINISTIC` and `CONSTANT`, the share outside the fallback region
for `DETERMINISTIC_CORE_FALLBACK`, none otherwise) and the tokens saved, with the arithmetic spelled out, e.g.

    TOKENS: 691600 tokens / 800 runs with a count (early + heldout half) = 864.5 per run; rule share 0.8775 (351 of 400 held-back cases lie outside
    the fallback region; the rest stays with the LLM); saved per run = 0.8775 x 864.5 = 758.6; saved over the log = 0.8775 x 691600 = 606879

Without a count the answer is `unknown`: nothing is estimated from text length, because a log line shows the source material at best, not the prompt,
the tool calls or the retries. Token counts never reach the rule or the verdict. Prices are out of scope: multiply by your own rate.

## Layout
    skills/replaceable/SKILL.md          the procedure the agent follows, the hard rules, the verdict table
    skills/replaceable/scripts/          prepare, run (= audit + replay + verdict), host_replay, selftest; fit, derive, thresholds, lean, stats
    test/                                unit tests that need no Lean: bound, verdict table, rule scan, solver, typing, tokens
    sz.py                                the line budget (code lines by `tokenize`, gate in MAX_LINE_COUNT)

## Development
    python3 -m unittest discover -s test && python3 skills/replaceable/scripts/selftest.py && python3 sz.py && ruff check .

Style: 2-space indentation, lines up to 150 characters, dense but not golfed, comments say why (`# NOTE:`), almost no docstrings. The line budget
is a feature: every script should stay readable in one sitting. See `SECURITY.md` for what the scripts do and do not protect against.
