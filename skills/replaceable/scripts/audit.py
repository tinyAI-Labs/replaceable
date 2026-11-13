# the audit before the exam: triage, conflict scan and rule fitting on the early half, with fixed stopping rules
# usage: python3 scripts/audit.py [--scope full|decision_step_only] [--derive "x.a * x.b"]... [--n-total N]
# NOTE: it reads schema.json, logs/early.jsonl and metadata.json, never the held-out half, stops at the first exit of the procedure and
# saves what it found to work/audit.json. Run alone it shows the fit without taking the exam; run.py calls it, the replay and the verdict.
# A rule that is weak on the fields as typed, or needs a fallback region, is fitted again with derived quantities (derive.py and any
# offered with --derive); they stay in the rule only if the fit with them cross-validates better, so a wrong guess costs nothing.
# The one question it asks instead of guessing is the scope. A weak rule is no question: it goes to the exam and ends INCONCLUSIVE.
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from typing import Any, Sequence
import lean, stats
from derive import KINDS, candidates, screen, widen
from fit import RISING, fit_report, forecast, learning, rank

AUDIT_FILE = stats.WORK_DIR / "audit.json"
LOG_KEYS = {"case_id", "ts", "typed_input", "output"} | stats.TOKEN_KEYS
MAX_DERIVE_RUNS = 2  # audit runs that may offer new --derive expressions; a run whose expressions Lean rejects does not count
SCORES = ("early_agreement", "cv_agreement")

def scripts_dir() -> str:
  # the directory of these scripts as the user should type it: relative when it is under the working directory
  here = Path(__file__).resolve().parent
  try: return str(here.relative_to(Path.cwd().resolve()))
  except ValueError: return str(here)

AUDIT_COMMAND, RUN_COMMAND, VERDICT_COMMAND = (f"python3 {scripts_dir()}/{name}.py" for name in ("audit", "run", "verdict"))
REPLAY_COMMAND = f"python3 {scripts_dir()}/replay.py Rule.lean"

def triage(rows: list[stats.Row], schema: stats.Schema, meta: dict[str, Any], heldout: Path, n_total: int | None, scope: str | None) -> dict:
  # is this a typed decision with enough cases, and which scope applies?
  pipeline = meta.get("pipeline") if isinstance(meta.get("pipeline"), dict) else {}
  extraction = bool(pipeline.get("extraction_step"))
  if n_total is None: n_total = meta.get("n_total") if isinstance(meta.get("n_total"), int) else None
  if n_total is None and heldout.is_file():  # NOTE: the held-back lines are counted without being parsed
    with heldout.open("rb") as stream: n_total = len(rows) + sum(1 for line in stream if line.strip())
  report: dict[str, Any] = {
    "n_early": len(rows), "n_total": n_total if n_total is not None else 2 * len(rows),
    "n_total_source": "counted" if n_total is not None else "assumed twice the early half (pass --n-total)",
    "scope": scope or ("decision_step_only" if extraction else "full"),
    "scope_source": "--scope" if scope else ("metadata.json: extraction_step" if extraction else "default: no extraction step is declared")}
  # source material next to the typed fields (in the log lines, or noted by prepare.py) and nothing says where the fields come from
  source = meta.get("source_fields") if isinstance(meta.get("source_fields"), dict) else {}
  extras = sorted({key for row in rows for key in row} - LOG_KEYS)
  names = extras or source.get("names") or []
  if names and not scope and not extraction:
    sample = json.dumps(next(row[extras[0]] for row in rows if extras[0] in row)) if extras else str(source.get("sample", ""))
    report["scope_question"] = (f"the logged cases also carry {', '.join(names)} (for example {sample[:160]}). If the loop read the typed fields out "
                                "of that material, the scope is decision_step_only; if the typed fields are the loop's direct input, it is full.")
  values = schema["output"]["values"]
  if not values:
    distinct = len({json.dumps(r["output"], sort_keys=True) for r in rows})
    return {**report, "exit": "OUT_OF_SCOPE", "exit_stage": "triage", "why": "The output has no fixed set of values (free text or creative output; "
            f"{distinct} distinct outputs in {len(rows)} early cases), so there is no typed decision to test."}
  if report["n_total"] < stats.MIN_TOTAL:
    return {**report, "exit": "INCONCLUSIVE", "exit_stage": "triage",
            "why": f"{report['n_total']} logged cases in total; at least {stats.MIN_TOTAL} are needed to hold back a usable test set."}
  for row in rows:  # every case must fit the frozen types
    try:
      lean.literal(schema, row["typed_input"])
      if row["output"] not in values: raise ValueError(f"output {row['output']!r} is not one of {values}")
    except (ValueError, KeyError, TypeError) as exc:
      return {**report, "exit": "INCONCLUSIVE", "exit_stage": "types", "why": f"Case {row.get('case_id')} does not fit the types: {exc}."}
  return report

def conflicts(rows: list[stats.Row], schema: stats.Schema) -> dict[str, Any]:
  # identical inputs with different outputs; for inputs that never repeat, the nearest-neighbour diagnostic
  scan = stats.conflict_scan(rows, stats.decision_fields(schema), stats.output_order(schema, rows), max_groups=None)
  reading, note = stats.conflict_reading(scan)
  groups = scan.pop("groups")
  report: dict[str, Any] = {"reading": reading, "note": note, **scan}
  if reading == "uninformative": report["near"] = stats.near_conflicts(rows, schema)
  if reading == "conflicts":
    clean = [g for g in groups if g["majority_share"] == 1.0]
    report["decomposition"] = {  # where the loop is and is not repeatable
      "inputs_always_same_output": len(clean), "share_of_cases_there": round(sum(g["n"] for g in clean) / len(rows), 4),
      "inputs_with_mixed_outputs": [{"input": g["input"], "n": g["n"], "counts": g["counts"]} for g in groups if g["majority_share"] < 1.0][:4]}
    report.update(exit="PROBABILISTIC", exit_stage="conflicts", why=(
      f"Identical typed inputs received different outputs: {scan['conflict_rate']:.1%} of the early cases whose input repeats disagree with "
      f"the majority answer for their input ({scan['conflicted_groups']} distinct inputs with mixed outputs)."))
  return report

def fit_best(rows: list[stats.Row], schema: stats.Schema, types_src: str, offered: list[str]) -> tuple[dict[str, Any], str]:
  # NOTE: the fitted rule and its report, on the fields as typed or with derived quantities where they cross-validate better. `offered`
  # are the reader's --derive expressions. Unless the rule on the typed fields is already strong and whole (no fallback region), the
  # built-in candidates are screened and those the pruned fit uses join them. Lean computes every quantity, so the numbers the fit sees
  # are the numbers the rule will compute. The fit with them stands only if it ranks higher (`fit.rank`).
  def diagnosed(report: dict[str, Any], *fitted: Any) -> dict[str, Any]:  # a weak rule's report gets the learning diagnostic of its rows
    return {**report, **(learning(*fitted) or {})} if forecast(report)[0] < stats.BOUND_DETERMINISTIC else report

  plain, plain_src = fit_report(rows, schema)
  what = {**(candidates(rows, schema) if rank(plain)[0] < 2 else {}), **{e: "offered with --derive" for e in offered}}
  searched = [e for e in what if e not in offered]
  if not offered and not searched: return diagnosed(plain, rows, schema), plain_src
  note: dict[str, Any] = {"without_them": {k: plain[k] for k in SCORES}}
  columns: dict[str, Sequence[int]] = {}  # expression -> its value for every early case
  for origin, group in (("offered", offered), ("searched", searched)):
    values = lean.run_derived(types_src, group, schema, [r["typed_input"] for r in rows], stats.WORK_DIR) if group else []
    if isinstance(values, str): note[f"{origin}_error"] = f"could not be evaluated on the early half: {values}"
    elif group: columns.update(zip(group, zip(*values)))
  found = screen(rows, schema, {e: columns[e] for e in searched if e in columns})
  if searched: note["searched"] = {"candidates": len(searched), "kinds": KINDS, "kept_for_the_fit": found}
  expressions = [e for e in offered if e in columns] + found
  names = [lean.derived_name(k) for k in range(len(expressions))]
  if not expressions: return diagnosed({**plain, "derived": {**note, "used_by_the_rule": []}}, rows, schema), plain_src
  wide = widen(rows, schema, {name: columns[e] for name, e in zip(names, expressions)})
  report, source = fit_report(*wide)
  note.update(expressions=dict(zip(names, expressions)), with_them={k: report[k] for k in SCORES})
  used = [k for k, name in enumerate(names) if name in report["fields_used"]]
  if not used or rank(report) <= rank(plain):
    stands = {**plain, "derived": {**note, "used_by_the_rule": [], "note": "no gain in cross-validated agreement: the rule without them stands"}}
    return diagnosed(stands, rows, schema), plain_src
  source = lean.derived_defs(expressions, used, [what[e] for e in expressions]) + re.sub(r"\bx\.(derived\d+)\b", r"(\1 x)", source)
  note.update(used_by_the_rule=[names[k] for k in used], offered_by_the_reader=[names[k] for k in used if expressions[k] in offered])
  return diagnosed({**report, "derived": note}, *wide), source

def weak_note(fit: dict[str, Any]) -> str:
  # what the audit computed about a weak rule: the derived quantities it tried on its own, and whether the agreement still rises with more cases
  said, derived, half = "", fit.get("derived", {}), fit.get("cv_agreement_on_half_the_cases")
  if "searched" in derived:
    used = len(derived["used_by_the_rule"])
    said += (f" Beyond thresholds and ratios of the typed fields, {derived['searched']['candidates']} derived quantities were tried "
             f"({KINDS}): {f'the rule uses {used} of them' if used else 'none improved the cross-validated agreement'}.")
  if half is not None and fit["cv_agreement"] is not None:
    said += (f" Fitted on half as many cases the rule cross-validates at {half:.1%}, on all of them at {fit['cv_agreement']:.1%}: "
             + ("the agreement is still rising with the number of cases, so this log is too short to settle the loop and a longer one may."
                if fit["cv_agreement"] - half >= RISING else
                "it has stopped rising, so a longer log of the same kind is unlikely to change the result."))
  return said

def fit_rule(rows: list[stats.Row], schema: stats.Schema, types_path: Path, rule_path: Path, derive: list[str]) -> dict[str, Any]:
  # NOTE: fit, write the fitted rule to Rule.lean (whatever stood there is overwritten), check it and run it on the early half with Lean
  types_src = types_path.read_text(encoding="utf-8")
  report, source = fit_best(rows, schema, types_src, derive)
  rule_path.write_text(source, encoding="utf-8")
  report["rule_file"], report["rule_sha256"], report["declares_fallback"] = str(rule_path), stats.sha256(source), lean.has_fallback(source)
  check = lean.check_rule(types_src, source, stats.WORK_DIR)
  if not check.compiled: return {**report, "lean": {"compiled": False, "violations": list(check.violations)}}
  run = lean.run_rule(types_src, source, schema, [r["typed_input"] for r in rows], stats.WORK_DIR)
  if run.outputs is None: return {**report, "lean": {"compiled": True, "error": run.failure}}
  report["lean"] = {"compiled": True, "early_agreement": round(sum(p == r["output"] for p, r in zip(run.outputs, rows)) / len(rows), 4)}
  if run.fallback is not None: report["lean"]["fallback"] = stats.fallback_stats(rows, run.outputs, run.fallback)
  return report

def next_step(state: dict[str, Any]) -> dict[str, str]:
  # what the procedure does next and why; `action` is verdict, broken, scope or replay. NOTE: a weak rule is not a stop: it goes to the replay.
  if "proposed" in state: return {"action": "verdict", "why": state["proposed"]["why"]}
  fit = state["fit"]
  if not fit["lean"].get("compiled") or "error" in fit["lean"]:
    return {"action": "broken", "why": "The fitted rule did not pass the check or could not be run on the early half. That is a fault of the "
                                       "installation or of the scripts, not a finding about the log; no verdict is written: "
                                       + json.dumps(fit["lean"])[:400]}
  if "scope_question" in state["triage"]:
    return {"action": "scope", "why": "The scope is your call and nothing declares it: " + state["triage"]["scope_question"]}
  agreement, measure = forecast(fit)
  seen = f"The rule matches {agreement:.1%} {measure}"
  if "derive_rejected" in state:
    seen = f"Lean rejected {state['derive_rejected']['expressions']} ({state['derive_rejected']['error'][:300]}); that did not use up a run. " + seen
  if agreement >= stats.BOUND_DETERMINISTIC:
    region = " It declares a fallback region (the part handed back to the LLM)." if fit["declares_fallback"] else ""
    return {"action": "replay", "why": f"{seen}.{region} The one-shot exam on the held-back half decides."}
  return {"action": "replay", "why": f"{seen}, under the {stats.BOUND_DETERMINISTIC} a deterministic verdict needs.{weak_note(fit)} The exam will "
                                     "most likely end INCONCLUSIVE; that is then the honest result, not a failure."}

def next_line(step: dict[str, str], via_run: bool = False) -> str:
  # the NEXT line: the exact command(s) for a step, phrased for audit.py or for run.py
  again = RUN_COMMAND if via_run else AUDIT_COMMAND
  return {"verdict": f"`{VERDICT_COMMAND}`",
          "broken": f"`python3 {scripts_dir()}/selftest.py` to check the installation",
          "scope": f"`{again} --scope decision_step_only` or `{again} --scope full`",
          "replay": f"`{RUN_COMMAND}`" + ("" if via_run else " (it takes the one-shot exam and writes the verdict)")}[step["action"]]

def audit(log="logs/early.jsonl", heldout="logs/heldout.jsonl", schema="schema.json", types="Types.lean", metadata="metadata.json", rule="Rule.lean",
          scope: str | None = None, n_total: int | None = None, derive: list[str] | None = None) -> dict[str, Any]:
  # NOTE: writes work/audit.json (and Rule.lean when a rule is fitted) and returns the saved state. Answers are remembered per log:
  # `scope` None means the scope chosen earlier, and `derive` adds to the expressions offered earlier, so one answer never undoes another.
  stats.WORK_DIR.mkdir(parents=True, exist_ok=True)
  if (stats.WORK_DIR / stats.RECEIPT_FILE).exists():
    stats.emit({"error": "the replay has already run; the rule is frozen", "next": VERDICT_COMMAND})
    raise SystemExit(1)
  rows, schema_ = stats.read_early(log, heldout), stats.read_schema(schema)
  early_sha, previous = stats.sha256(Path(log).read_bytes()), stats.read_json(AUDIT_FILE)
  if previous.get("early_sha256") != early_sha: previous = {}  # a different log: nothing carries over
  scope = scope or previous.get("scope_choice")
  kept: list[str] = list(previous.get("derive", []))  # the quantities offered so far stay on offer
  offered = [e for e in dict.fromkeys(derive or []) if e not in kept]
  tried: list[list[str]] = list(previous.get("derive_tried", []))
  if offered and len(tried) >= MAX_DERIVE_RUNS:
    stats.emit({"error": f"the {MAX_DERIVE_RUNS} --derive runs are used; nothing was changed. Go on without a new --derive.", "derive_tried": tried,
                "next": RUN_COMMAND})
    raise SystemExit(1)
  state: dict[str, Any] = {"early_sha256": early_sha, "scope_choice": scope, "derive": kept, "derive_tried": tried, "derive_runs": len(tried),
                           "triage": triage(rows, schema_, stats.read_json(metadata), Path(heldout), n_total, scope),
                           "tokens": stats.token_stats(rows)}
  if "exit" not in state["triage"]: state["conflicts"] = conflicts(rows, schema_)
  if "exit" not in state.get("conflicts", {"exit": 1}):
    lean.require_lean()
    if offered:  # NOTE: Lean must accept the new expressions before they join the fit; a rejected set does not use up a run
      answer = lean.run_derived(Path(types).read_text(encoding="utf-8"), offered, schema_, [r["typed_input"] for r in rows[:1]], stats.WORK_DIR)
      if isinstance(answer, str): state["derive_rejected"] = {"expressions": offered, "error": f"not usable as Nat expressions of x: {answer}"}
      else:
        kept += offered
        tried.append(offered)
        state["derive_runs"] = len(tried)
    state["fit"] = fit_rule(rows, schema_, Path(types), Path(rule), kept)
  else:
    stage = state.get("conflicts", state["triage"])
    state["proposed"] = {"verdict": stage["exit"], "exit_stage": stage["exit_stage"], "why": stage["why"]}
  state["step"] = next_step(state)
  stats.write_text_atomic(AUDIT_FILE, json.dumps(state, indent=2))
  return state

def view(state: dict[str, Any]) -> dict[str, Any]:
  # a short view of the state for the terminal; work/audit.json has everything
  shown = {"saved": str(AUDIT_FILE), "triage": {k: v for k, v in state["triage"].items() if k in ("n_total", "scope", "scope_source")}}
  if "scope_question" in state["triage"]: shown["triage"]["scope"] = "UNDECIDED"
  if "conflicts" in state:
    keep = ("reading", "note", "conflict_rate", "repeat_share", "conflicted_groups", "example_pairs", "near", "decomposition")
    shown["conflicts"] = {k: v for k, v in state["conflicts"].items() if k in keep}
  if "derive_rejected" in state: shown["derive_rejected"] = state["derive_rejected"]
  if "fit" in state:
    shown["fit"] = {k: v for k, v in state["fit"].items() if k not in ("slice", "rule_sha256", "residual")}
    shown["residual"] = state["fit"]["residual"]
  if "proposed" in state: shown["proposed"] = state["proposed"]
  if state.get("tokens", {}).get("with_tokens"): shown["tokens_early"] = state["tokens"]
  return shown

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="The audit before the exam: triage, conflict scan and rule fitting on the early half.")
  parser.add_argument("--scope", choices=("full", "decision_step_only"), default=None,
                      help="decision_step_only when the typed inputs were extracted from documents, free text or tool results; remembered")
  parser.add_argument("--n-total", type=int, default=None, help="logged cases in total (early + held back), if no metadata.json states it")
  parser.add_argument("--derive", action="append", default=None, metavar="LEAN_EXPR", help='a quantity you know the policy uses, as a Nat-valued '
                      'Lean expression of x, e.g. "x.account_no %% 10"; repeatable; quantities offered in earlier runs stay on offer')
  args = parser.parse_args()
  state = audit(**vars(args))
  stats.emit(view(state), trim=("residual",))
  print("NEXT: " + next_line(state["step"]))  # NOTE: NEXT and WHY come before the rule so a host that cuts long output keeps them
  stats.show("WHY: " + state["step"]["why"])
  if "fit" in state: stats.show("--- Rule.lean ---\n" + Path("Rule.lean").read_text(encoding="utf-8").rstrip())
