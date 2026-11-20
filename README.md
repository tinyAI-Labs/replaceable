# replaceable

Is this agent loop doing work a fixed rule could do? `replaceable` is an agent skill (a `SKILL.md` plus standard-library Python scripts) that answers
from the loop's own log: it fits a small, readable rule to the earlier half of the logged cases, writes it as a total Lean 4 function, replays it once
on the later half it has never seen, and returns one of seven verdicts with the evidence. If the log says what each run cost, it also says how many
tokens the replacement would save.

| Verdict | Meaning |
|---|---|
| `DETERMINISTIC` | a rule reproduces the held-back cases (95% lower bound on agreement >= 0.95, clearly better than a constant) |
| `DETERMINISTIC_CORE_FALLBACK` | a rule covers most cases; a declared region is handed back to the LLM |
| `DETERMINISTIC_LEARNED` | no readable rule, but a model learned from the early half reproduces the held-back half (99% lower bound >= 0.95, clearly better than a constant); the model is discarded, see below |
| `CONSTANT` | every logged case has the same output |
| `PROBABILISTIC` | identical inputs received different outputs |
| `INCONCLUSIVE` | not enough evidence (too few cases, or no rule found); never read as "probabilistic" |
| `OUT_OF_SCOPE` | the output is free text, not a typed decision |

## Install
Needs Python 3.12+ (no packages) and Lean 4 on `PATH` (`curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y`, then open a new shell or run `export PATH="$HOME/.elan/bin:$PATH"`; the tested version is in
`lean-toolchain`). In Claude Code:

    /plugin marketplace add tinyAI-Labs/replaceable
    /plugin install replaceable@tinyai-labs

For another agent, copy `skills/replaceable/` to where it keeps skills. Then check the install (macOS or Linux):

    S=$(ls -d ~/.claude/plugins/cache/tinyai-labs/replaceable/*/skills/replaceable/scripts | tail -1)   # in a clone: S=skills/replaceable/scripts
    python3 $S/selftest.py                             # nine generated loops with known answers, about a minute

## Use
Give the agent a log and ask whether the loop is replaceable, or run the two commands yourself, in the directory that holds the log:

    python3 $S/prepare.py raw.jsonl   # types the log, splits it by time into logs/early.jsonl and logs/heldout.jsonl
    python3 $S/run.py                 # audit, one-shot replay, verdict: ends with an Answer and a Tokens line (add --why for VERDICT / WHY / TOKENS)

To try it first, copy `examples/ticket_triage/raw.jsonl` (6,000 runs of a support-ticket router that learned its routing from corrections) into an empty directory: the
output ends with `Answer: Yes, deterministic. Checked on the runs after the loop held 95% accuracy ...` and a `Tokens:` line with what the loop spends, now and while it was learning. Add `--why` to `run.py` (it resumes, nothing is replayed again) to also see the `VERDICT:`, `WHY:` and `TOKENS:` lines; an agent running the skill prints them only if you ask why. Four more examples are below.

`raw.jsonl` has one logged run per line: `{"typed_input": {...}, "output": ..., "ts": ..., "tokens": 1840, "correct": true}`. `typed_input` holds numbers,
booleans, dates and categories; `ts`, `case_id`, the token count and `correct` are optional. At least 150 runs are needed. `run.py` asks at most one question (the scope:
were the typed fields extracted from documents by the loop itself?) and prints the exact command to answer it. `Rule.lean` is always the
rule the solver fitted: `run.py` overwrites an edited one before the exam, and an edit after the exam turns the verdict INCONCLUSIVE.

## Examples
Five synthetic loops of the kind a company runs, in `examples/<name>/raw.jsonl`: each starts around 60% right and corrects itself from feedback (a human
override, a check) until it is right at least 95% of the time, or does not get there. Run any of them as above, or all of them with `sh examples/check.sh`.

| example | the loop | verdict |
|---|---|---|
| `ticket_triage` | routes support tickets to a queue; one routing note per category, learned from corrections; enterprise urgent tickets escalate | DETERMINISTIC, once it has converged |
| `expense_approval` | approves or escalates by a limit for each department, learned from reviewer overrides; a person decides the big amounts case by case | DETERMINISTIC_CORE_FALLBACK |
| `pr_labeler` | labels pull requests small, medium or large with its own limits for each area and author type: too many branches to read, a learned model reproduces it | DETERMINISTIC_LEARNED |
| `refund_agent` | improves but plateaus near 80%: the same inputs still get different answers | PROBABILISTIC (it never settles at 95%) |
| `lead_router_memory` | reaches 95%, but enterprise leads go to the two reps in a rotation the loop keeps in its notes and the log does not carry | INCONCLUSIVE (hidden state) |

The answer says after which run the loop held 95% (or that its feedback never does, or has not settled), and the `Tokens:` line says what the runs before that cost compared with the converged loop.
Something to try: delete the `correct` field from every line of `ticket_triage`: the exam then runs on the whole log, the learning phase is mixed in,
and the same inputs now got different answers at different times: it comes out PROBABILISTIC.

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
          "tokens": run["usage"]["total_tokens"],            # optional, for the Tokens line
          "correct": run["feedback"]["passed"]}) + "\n")   # optional: was this run right, per the human override or the check

- Fields are numbers, booleans, dates (`2024-03-21`) and categories. `prepare.py` stops at anything else (free text, identifiers, nested data) and
  names the field: convert it (money to whole cents, text to a category) or leave it out with `--drop FIELD`. Drop what the policy cannot depend on.
- At least 150 runs (after the learning phase, if the log has one); a policy with many branches needs about 1000 or more before a rule can pass, and about 2000 for the learned check.
- `correct` is the loop's own feedback on a run: a human override, a test, an eval score turned into pass or fail, as tracing tools record it. With it on
  every line, the skill sets aside the runs before the loop held 95% (its learning phase), examines the rest, and reports what the learning phase cost
  per run against the converged loop. A loop whose feedback is still moving (rising, or at 95% for fewer than 150 runs) is `INCONCLUSIVE`: it has no fixed
  policy yet. One that stays flat below 95% is examined on its latest half. Without `correct`, the whole log is examined, and a loop that is still
  learning looks like one that drifted or guesses (`PROBABILISTIC`, or `INCONCLUSIVE`). Under 150 runs `correct` is ignored.

## Tokens and savings
Log a token count per run as `tokens` (the run's total), or as the SDKs report it: `usage.input_tokens` + `usage.output_tokens` (cache tokens are
added), `prompt_tokens` + `completion_tokens`, or `total_tokens`. The verdict then carries a `tokens` block and a `TOKENS:` line: tokens per run,
the total over the log, the share of runs the rule would take over (all for `DETERMINISTIC` and `CONSTANT`, the share outside the fallback region
for `DETERMINISTIC_CORE_FALLBACK`, none otherwise: `DETERMINISTIC_LEARNED` hands over no rule) and the tokens saved, with the arithmetic spelled out, e.g.

    TOKENS: 691600 tokens / 800 runs with a count (early + heldout half) = 864.5 per run; rule share 0.8775 (351 of 400 held-back cases lie outside
    the fallback region; the rest stays with the LLM); saved per run = 0.8775 x 864.5 = 758.6; saved over the log = 0.8775 x 691600 = 606879

Without a count the answer is `unknown`: nothing is estimated from text length, because a log line shows the source material at best, not the prompt,
the tool calls or the retries. Token counts never reach the rule or the verdict. The `Tokens:` line quotes the tokens per run and over the log, and a monthly pace when the log's `ts` are ISO timestamps that span at least a day (at the pace of the early half; a counter says nothing about time). It quotes a cost only when you give a price: `run.py --usd-per-mtok 3` (what you pay per million tokens, one blended rate); the skill has no price table.

## The learned check
A rule is limited to 16 branches so a person can read it. Some loops are a function of their inputs and still have more branches than that. For those
the skill learns a model, gradient-boosted trees in pure Python (`scripts/witness/`, standard library only), on the early half, scores it once on the
same held-back half in the same replay, and keeps only the numbers. The model is trained in memory and discarded: nothing is written, nothing ships, and
it cannot stand in for the loop. `DETERMINISTIC_LEARNED` means the typed fields decide the output, so the policy can be written down; it is never given
instead of a verdict a rule earned, only where the rule-only skill would say `INCONCLUSIVE`, and its bar is stricter (99% lower bound, not 95%).
Delete `scripts/witness/` and the skill is the rule-only skill. It needs large logs (a 95% accuracy cannot clear a 95% bound on a few hundred cases).

## Layout
    skills/replaceable/SKILL.md          the procedure the agent follows, the hard rules, the verdict table
    skills/replaceable/scripts/          prepare, run (= audit + replay + verdict), host_replay, selftest; fit, derive, thresholds, lean, stats
    skills/replaceable/scripts/witness/   the optional learned check: boost (the learner) and exam (train, score once, keep the numbers)
    examples/                            five loops with a known answer, each with the verdict it must end in; check.sh runs them all
    test/                                unit tests that need no Lean: bound, verdict table, rule scan, solver, typing, tokens
    sz.py                                the line budget (code lines by `tokenize`, gate in MAX_LINE_COUNT)

## Development
    python3 -m unittest discover -s test && python3 skills/replaceable/scripts/selftest.py && sh examples/check.sh && python3 sz.py && ruff check .

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
- **More:** [tinyailabs.com](https://tinyailabs.com/).
