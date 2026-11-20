# the verdict as code: computed from work/audit.json and work/receipt.json, written to verdict.json (the table is in SKILL.md)
# usage: python3 scripts/verdict.py [--scope full|decision_step_only] [--note "..."]
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any
import stats
from stats import BOUND_DETERMINISTIC, MIN_HELDOUT
from replay import read_receipt
from audit import AUDIT_COMMAND, AUDIT_FILE, REPLAY_COMMAND, RUN_COMMAND, VERDICT_COMMAND, weak_note

SCOPED = ("DETERMINISTIC", "DETERMINISTIC_LEARNED", "CONSTANT")

ASK = "Do you want to reduce your token spend for this agentic loop to zero? Find out how: https://tinyailabs.com/"
# NOTE: the owner's whole report is two lines: one answer per verdict and what the loop spends. How the verdict came about is printed only on --why.
MIN_PHASE = 30  # runs a phase needs before its token cost is compared with the other's
ANSWERS = {
  "DETERMINISTIC": "Yes, deterministic.", "CONSTANT": "Yes, constant.",
  "DETERMINISTIC_LEARNED": "Yes, deterministic (no readable rule).", "PROBABILISTIC": "No, not deterministic.",
  "INCONCLUSIVE": "Cannot tell from this log.", "OUT_OF_SCOPE": "Not applicable: the output is free text, not a typed decision."}

def pct(share: float) -> str:
  return f"{int(share * 100)}%"  # rounded down: a figure below the bar must not be shown as the bar

def moving_text(moving: dict[str, float]) -> str:
  if moving.get("drift"):
    return f"it is still moving ({pct(moving['before'])} early in its last {moving['runs']} runs, {pct(moving['recent'])} at the end)"
  return f"it changed {moving['runs']} runs before the end of the log ({pct(moving['recent'])} right since, {pct(moving['before'])} before)"

def answer_line(result: dict[str, Any]) -> str:
  # the answer, and when the log came with the loop's own feedback, which part of the log it is about
  learned, text = result.get("learning"), ANSWERS[result["verdict"]]
  if learned and learned.get("from_run"):
    text += f" Checked on the runs after the loop held 95% accuracy (after run {learned['from_run']:,} of {learned['runs']:,})."
  elif learned and learned.get("unsettled"):
    text += f" Its feedback has not settled: {moving_text(learned['unsettled'])}. Run this again once it has been steady for 300 or more runs."
  elif learned and not learned["converged"]:
    text += (f" Its feedback holds at {pct(learned['accuracy'])}, below 95%: "
             f"{'its steady stretch' if learned['examined_from'] else 'the whole log'} was checked.")
  if learned and learned.get("trimmed"): text += " (--max-rows: only its latest runs.)"
  return f"Answer: {text}"

def learning_cost(tokens: dict[str, Any]) -> tuple[float, float] | None:
  # (tokens per run in the learning phase, how many times the converged loop's cost) once both phases are long enough to compare
  spent = (tokens.get("learning") or {}).get("tokens") or {}
  if spent.get("with_tokens", 0) >= MIN_PHASE and tokens["cases_with_tokens"] >= MIN_PHASE and tokens["per_run"]:
    return spent["mean"], spent["mean"] / tokens["per_run"]
  return None

def tokens_line(tokens: dict[str, Any], usd: float | None = None, ask: bool = True) -> str:
  # the second line: what the loop spends, from the log's own counts (a price only if given, a pace only if the log has dates), then the question,
  # asked only of a loop that could be replaced
  tail = f" {ASK}" if ask else ""
  if not tokens["known"]: return f"Tokens: not in this log.{tail}"
  learned = tokens.get("learning") or {}
  runs = ("latest runs" if learned.get("trimmed") else "runs after it converged" if "from_run" in learned
          else "runs in its steady stretch" if learned.get("examined_from") else "logged runs")
  said = f"this loop used {tokens['per_run']:,.0f} per run, {tokens['logged']:,} over its {tokens['cases_with_tokens']:,} {runs}"
  if "per_month" in tokens: said += f", at that pace about {tokens['per_month']:,} a month"
  if cost := learning_cost(tokens):  # what a budget made from the converged cost leaves out
    said += (f"; the {learned['from_run']:,} runs before it converged ({pct(learned['accuracy_before'])} accurate) used {cost[0]:,.0f} per run, "
             f"{cost[1]:.1f}x as much")
  total, span = (tokens["per_month"], "a month") if "per_month" in tokens else (tokens["logged"], "over the logged runs")
  return f"Tokens: {said}" + (f" (about ${total * usd / 1e6:,.2f} {span} at ${usd:g} per million tokens)" if usd else "") + f".{tail}"

def verdict_from_replay(replay: dict[str, Any]) -> tuple[str, str]:
  # NOTE: (verdict, reason) for a replay report or receipt. L is the exact one-sided 95% lower bound on the held-out agreement. The rows
  # are tried in this order, and a weak rule is never evidence of a probabilistic loop: it ends INCONCLUSIVE.
  if "failure" in replay: return "INCONCLUSIVE", f"The rule could not be run on the held-back cases ({replay['failure']}); the one replay is used."
  n, low, base = replay["n_heldout"], replay["lower_bound"], replay["baseline"]
  seen = f"The rule matched {replay['n_match']} of {n} held-back cases (95% lower bound {low})"
  if n < MIN_HELDOUT: return "INCONCLUSIVE", f"Only {n} held-back cases; at least {MIN_HELDOUT} are needed."
  if replay["all_outputs_constant"] and low >= BOUND_DETERMINISTIC: return "CONSTANT", f"Every logged case has the same output. {seen}."
  if low >= BOUND_DETERMINISTIC:
    if stats.beats_baseline(low, base): return "DETERMINISTIC", f"{seen}; always answering the most common output would match {base}."
    return "INCONCLUSIVE", (f"{seen}, but always answering the most common output already matches {base}: "
                            "there are too few cases of the other outputs to tell a rule from a constant.")
  model = replay.get("model")  # NOTE: last of the passing rows: the model turns an INCONCLUSIVE into a verdict, never one verdict into another
  if model and model["lower_bound"] >= BOUND_DETERMINISTIC and stats.beats_baseline(model["lower_bound"], model["baseline"]):
    return "DETERMINISTIC_LEARNED", f"{seen}: no readable rule reaches {BOUND_DETERMINISTIC}. {model['why']}"
  return "INCONCLUSIVE", f"{seen}: below {BOUND_DETERMINISTIC}. No rule reaches it; that alone does not show the loop is probabilistic."

def build(state: dict[str, Any], receipt: dict[str, Any] | None, rule_src: str | None, scope: str | None) -> dict[str, Any]:
  # the verdict object, or {"error": ..., "needs": the missing step} when it cannot be given yet
  moving, proposed = (state.get("learning") or {}).get("unsettled"), state.get("proposed")
  if moving and (proposed or {}).get("verdict") != "OUT_OF_SCOPE":  # NOTE: a policy still being corrected cannot be tested, whatever an exam says
    return {"verdict": "INCONCLUSIVE", "exit_stage": "feedback", "rationale": (
      f"The loop's feedback has not settled: {moving_text(moving)}. A policy that is still being corrected has nothing fixed to replace; run the "
      "check again once it has been steady for 300 or more runs.")}
  if proposed:  # triage or conflicts exit: no rule is involved
    verdict = {"verdict": proposed["verdict"], "exit_stage": proposed["exit_stage"], "rationale": proposed["why"]}
    if proposed["verdict"] == "PROBABILISTIC":
      verdict["conflict_pairs"] = state["conflicts"]["example_pairs"]
      split = state["conflicts"].get("decomposition")
      if split and split["share_of_cases_there"] > 0:
        verdict["deterministic_subregion"] = (
          f"{split['share_of_cases_there']:.0%} of early cases have inputs that always received the same output "
          f"({split['inputs_always_same_output']} distinct inputs); a fixed rule could cover those and leave the rest to the LLM.")
    return verdict
  if receipt is None: return {"error": f"the held-out replay has not run. Run it as a command on its own: {REPLAY_COMMAND}", "needs": "replay"}
  ran = "failure" not in receipt
  if ran and (rule_src is None or stats.sha256(rule_src) != receipt.get("rule_sha256")):
    return {"verdict": "INCONCLUSIVE", "exit_stage": "replay",
            "rationale": "Rule.lean changed after the one-shot replay, so the replay no longer describes it. The evidence is void."}
  name, why = verdict_from_replay(receipt)
  verdict: dict[str, Any] = {"verdict": name, "exit_stage": "replay", "rationale": why}
  if ran: verdict.update({k: receipt[k] for k in ("n_heldout", "lower_bound", "agreement", "baseline")})
  if ran and "sampled_from" in receipt:
    verdict["rationale"] += f" The replay used a fixed sample of {receipt['n_heldout']} of {receipt['sampled_from']} held-back cases."
  fit, scan = state.get("fit", {}), state.get("conflicts", {})
  if name in SCOPED:
    if not scope and "scope_question" in state["triage"]:  # NOTE: a deterministic claim needs the scope, and it is the reader's call
      return {"error": f"decide the scope first: {state['triage']['scope_question']} Then: `{VERDICT_COMMAND} --scope decision_step_only` or "
                       f"`{VERDICT_COMMAND} --scope full`", "needs": "scope"}
    verdict["scope"] = scope or state["triage"]["scope"]
    if verdict["scope"] == "decision_step_only":
      verdict["rationale"] += " Scope: the decision step on typed fields only; the extraction step that produces those fields stays probabilistic."
  if name == "INCONCLUSIVE" and ran and receipt["lower_bound"] < BOUND_DETERMINISTIC: verdict["rationale"] += weak_note(fit)
  if name == "INCONCLUSIVE" and (scan.get("near") or {}).get("neighbour_disagreement") is not None and ran:
    verdict["rationale"] += (f" Diagnostic: {scan['near']['neighbour_disagreement']:.0%} of early cases differ from their nearest neighbour (random "
                             f"pairs: {scan['near']['chance']:.0%}); a high share points to noise or an unlogged input rather than a missed rule.")
  if scan.get("reading") == "one_group":
    verdict["rationale"] += (f" Note: one repeated input received different outputs ({scan['example_pairs']}); "
                             "a single input is not enough to call the loop probabilistic.")
  if "rule_sha256" in receipt: verdict["rule_sha256"] = receipt["rule_sha256"]
  return verdict

def completed_by(state: dict[str, Any]) -> str:
  # who shaped the rule behind the verdict: the scripts alone, or a quantity the reader offered
  return "agent-derive" if state.get("fit", {}).get("derived", {}).get("offered_by_the_reader") else "script-default"

def token_block(state: dict[str, Any], receipt: dict[str, Any] | None, verdict: dict[str, Any]) -> dict[str, Any]:
  # NOTE: computed after the verdict and from it, never the other way round. Tokens per run come from the counts the log states (early
  # half from the audit, held-back half as aggregates from the replay receipt); the saving is that mean times the share of runs the rule
  # takes over. No count in the log means unknown: nothing is estimated from text length.
  halves = {name: t for name, t in (("early", state.get("tokens")), ("heldout", (receipt or {}).get("tokens"))) if t and t["with_tokens"]}
  whole = state.get("log_tokens")
  if receipt is None and whole and whole["with_tokens"]: halves = {"log": whole}  # an early exit: no replay, but the count is known
  if not halves: return {"known": False, "note": "no token count in the log lines; add `tokens` (or `usage`) per case to get token use and savings"}
  n, total, name = sum(t["with_tokens"] for t in halves.values()), sum(t["total"] for t in halves.values()), verdict["verdict"]
  if name in ("DETERMINISTIC", "CONSTANT"): share, why = 1.0, f"{name}: the rule takes over every run"
  else: share, why = 0.0, f"{name}: no rule replaces the loop"
  mean = round(total / n, 1)
  block = {"known": True, "per_run": mean, "logged": total, "cases_with_tokens": n, "halves": halves, "rule_share": share,
           "saved_per_run": round(share * mean, 1), "saved_logged": round(share * total)}
  if learned := state.get("learning"):  # the loop, seen from the runs before it converged
    kept = ("runs", "from_run", "accuracy_before", "examined_from", "trimmed")
    block["learning"] = {k: learned[k] for k in kept if k in learned} | {"tokens": learned.get("tokens")}
  if state.get("runs_per_day"): block["per_month"] = round(mean * state["runs_per_day"] * 30)  # at the pace of the early half of the log
  where = "the log examined" if "log" in halves else " + ".join(halves) + " half"
  block["arithmetic"] = (f"{total} tokens / {n} runs with a count ({where}) = {mean} per run; rule share {share} ({why}); saved "
                         f"per run = {share} x {mean} = {block['saved_per_run']}; saved over the log = {share} x {total} = {block['saved_logged']}")
  if share and verdict.get("scope") == "decision_step_only":
    block["note"] = "upper bound: the extraction step stays with the LLM, and the log does not split its tokens from the decision step's"
  return block

def write(scope: str | None = None, note: str | None = None, rule="Rule.lean", out="verdict.json") -> dict[str, Any]:
  # compute the verdict and write it to `out`; returns it, or {"error": ...} with nothing written
  if not AUDIT_FILE.is_file(): return {"error": f"{AUDIT_FILE} not found. Run first: {AUDIT_COMMAND}", "needs": "audit"}
  state, receipt, rule_path = stats.read_json(AUDIT_FILE), read_receipt(), Path(rule)
  verdict = build(state, receipt, rule_path.read_text(encoding="utf-8") if rule_path.is_file() else None, scope)
  if "error" in verdict: return verdict
  if note: verdict["rationale"] += f" Note: {note}"
  verdict["completed_by"] = completed_by(state)
  verdict["tokens"] = token_block(state, receipt, verdict)
  if state.get("learning"): verdict["learning"] = {k: v for k, v in state["learning"].items() if k != "tokens"}
  stats.write_text_atomic(Path(out), json.dumps(verdict, indent=2))
  return verdict

def conclude(scope: str | None = None, note: str | None = None, why: bool = False, usd: float | None = None) -> int:
  # write verdict.json and print the answer and the token line (priced at `usd` per million tokens, if given); with `why` first how the
  # procedure ended (VERDICT, WHY, TOKENS, NEXT).
  # Returns the exit code, 1 when no verdict could be written.
  result = write(scope, note)
  if "error" in result:
    needs_scope = result.get("needs") == "scope"  # the replay ran before the scope was settled
    print(f"NEXT: `{RUN_COMMAND} --scope decision_step_only` or `{RUN_COMMAND} --scope full`" if needs_scope else "NEXT: see WHY")
    stats.show("WHY: " + result["error"])
    return 0 if needs_scope else 1
  if why:
    stats.emit({"written": "verdict.json", **result})
    print(f"VERDICT: {result['verdict']}" + (f" (scope: {result['scope']})" if "scope" in result else ""))
    stats.show("WHY: " + result["rationale"])
    print(f"TOKENS: {result['tokens'].get('arithmetic', 'unknown')}" + (f" ({result['tokens']['note']})" if "note" in result["tokens"] else ""))
    print("NEXT: nothing to run; verdict.json is written. Report it as it is." + (
      " A person confirms that Rule.lean matches the written policy before it replaces the loop."
      if "scope" in result and result["verdict"] != "DETERMINISTIC_LEARNED" else ""))
  import report  # here, not on top: report reads this module
  report.write(result, usd)
  print(report.terminal(result, usd))
  return 0

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="The verdict as code: compute it from the recorded numbers and write verdict.json.")
  parser.add_argument("--scope", choices=("full", "decision_step_only"), default=None, help="override the scope recorded by audit.py")
  parser.add_argument("--note", default=None, help="your own remark, appended to the rationale")
  args = parser.parse_args()
  verdict = write(args.scope, args.note)
  stats.emit(verdict if "error" in verdict else {"written": "verdict.json", **verdict})
  sys.exit(1 if "error" in verdict else 0)
