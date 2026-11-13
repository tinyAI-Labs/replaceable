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

For another agent, copy `skills/replaceable/` to where it keeps skills. Then check the install (macOS or Linux):

    S=$(ls -d ~/.claude/plugins/cache/tinyai-labs/replaceable/*/skills/replaceable/scripts | tail -1)   # in a clone: S=skills/replaceable/scripts
    python3 $S/selftest.py                             # nine generated loops with known answers, about a minute

## Use
Give the agent a log and ask whether the loop is replaceable, or run the two commands yourself, in the directory that holds the log:

    python3 $S/prepare.py raw.jsonl   # types the log, splits it by time into logs/early.jsonl and logs/heldout.jsonl
    python3 $S/run.py                 # audit, one-shot replay, verdict: ends with VERDICT / WHY / TOKENS and verdict.json

To try it first, copy `examples/raw.jsonl` (600 runs of a shipping loop) into an empty directory: the output ends with the lines in
`examples/expected.txt`.

`raw.jsonl` has one logged run per line: `{"typed_input": {...}, "output": ..., "ts": ..., "tokens": 1840}`. `typed_input` holds numbers, booleans,
dates and categories; `ts`, `case_id` and the token count are optional. At least 150 runs are needed. `run.py` asks at most one question (the scope:
were the typed fields extracted from documents by the loop itself?) and prints the exact command to answer it.

## Your own log
One JSON object per line (JSONL); a JSON array or a CSV has to be converted first. Each line is one run of the loop: the typed fields the decision
was made from, and the decision. From a trace or message-list export, roughly:

    import json
    with open("raw.jsonl", "w") as out:
      for run in json.load(open("export.json")):            # one entry per loop run
        args = run["messages"][0]["content"]                # wherever the structured input lives: tool arguments, a form, a ticket record
        out.write(json.dumps({
          "typed_input": {"amount_cents": round(args["amount"] * 100), "country": args["country"], "is_member": args["member"]},
          "output": run["messages"][-1]["content"].strip(),  # the decision, one of a fixed set of values
          "ts": run["started_at"],                           # orders the split; without it, file order
          "tokens": run["usage"]["total_tokens"]}) + "\n")  # optional, for the TOKENS line

- Fields are numbers, booleans, dates (`2024-03-21`) and categories. `prepare.py` stops at anything else (free text, identifiers, nested data) and
  names the field: convert it (money to whole cents, text to a category) or leave it out with `--drop FIELD`. Drop what the policy cannot depend on.
- At least 150 runs; a policy with many branches needs about 1000 or more before a rule can pass.

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
    examples/                            a 600-run log and the three lines its run ends with
    test/                                unit tests that need no Lean: bound, verdict table, rule scan, solver, typing, tokens
    sz.py                                the line budget (code lines by `tokenize`, gate in MAX_LINE_COUNT)

## Development
    python3 -m unittest discover -s test && python3 skills/replaceable/scripts/selftest.py && python3 sz.py && ruff check .

Style: 2-space indentation, lines up to 150 characters, dense but not golfed, comments say why (`# NOTE:`), almost no docstrings. The line budget
is a feature: every script should stay readable in one sitting. See `SECURITY.md` for what the scripts do and do not protect against.

## Alpha
This is an alpha: the verdicts are conservative and the edges are rough. What helps most is the result on a real loop.

- **Send:** an issue with `verdict.json` and the `WHY:` line, and whether the verdict matches what you know about the loop.
- **Do not send:** logs, `Rule.lean` or `schema.json`: they carry your field names and your policy.
- **Limits:** typed decisions only (free text is `OUT_OF_SCOPE`); 150 runs at least, about 1000 or more for many-branch policies; `INCONCLUSIVE`
  is common and safe (it never means "probabilistic"); macOS and Linux only (POSIX); Python 3.12+ and Lean 4 are required; the rule scan is not an
  OS sandbox (`SECURITY.md`); a person signs off `Rule.lean` against the written policy before it replaces anything.
- **Update:** `/plugin marketplace update tinyai-labs`. No telemetry: the scripts make no network calls.
- **Join the alpha:** [sign up here](https://forms.gle/Wg8fFDhCu84mF8Mu7) to hear about updates.
