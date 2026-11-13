# the verdict as code: computed from work/audit.json and work/receipt.json, written to verdict.json (the table is in SKILL.md)
# usage: python3 scripts/verdict.py [--scope full|decision_step_only] [--note "..."]
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any
import stats
from stats import BOUND_CORE, BOUND_DETERMINISTIC, MIN_HELDOUT
from replay import read_receipt
from audit import AUDIT_COMMAND, AUDIT_FILE, REPLAY_COMMAND, RUN_COMMAND, VERDICT_COMMAND, weak_note

SCOPED = ("DETERMINISTIC", "DETERMINISTIC_CORE_FALLBACK", "CONSTANT")

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
  fb = replay.get("fallback")
  if low >= BOUND_CORE and fb and fb["inside"] >= 1 and fb["outside"] >= MIN_HELDOUT and fb["outside_lower_bound"] >= BOUND_DETERMINISTIC:
    return "DETERMINISTIC_CORE_FALLBACK", (
      f"{seen}; outside the declared fallback region it matched {fb['outside'] - fb['misses_outside']} of {fb['outside']} "
      f"(95% lower bound {fb['outside_lower_bound']}), and {fb['inside']} held-back cases fall inside the region.")
  if low >= BOUND_CORE:
    why = "no fallback region was declared" if not fb else (
      f"the fallback region does not contain the misses ({fb['misses_outside']} misses in the {fb['outside']} cases outside it, "
      f"95% lower bound {fb['outside_lower_bound']}; {BOUND_DETERMINISTIC} on at least {MIN_HELDOUT} cases is needed)")
    return "INCONCLUSIVE", f"{seen}: below {BOUND_DETERMINISTIC}, and {why}."
  return "INCONCLUSIVE", f"{seen}: below {BOUND_CORE}. No rule was found; that alone does not show the loop is probabilistic."

def build(state: dict[str, Any], receipt: dict[str, Any] | None, rule_src: str | None, scope: str | None) -> dict[str, Any]:
  # the verdict object, or {"error": ..., "needs": the missing step} when it cannot be given yet
  proposed = state.get("proposed")
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
  fit, scan = state.get("fit", {}), state.get("conflicts", {})
  if name in SCOPED:
    if not scope and "scope_question" in state["triage"]:  # NOTE: a deterministic claim needs the scope, and it is the reader's call
      return {"error": f"decide the scope first: {state['triage']['scope_question']} Then: `{VERDICT_COMMAND} --scope decision_step_only` or "
                       f"`{VERDICT_COMMAND} --scope full`", "needs": "scope"}
    verdict["scope"] = scope or state["triage"]["scope"]
    if verdict["scope"] == "decision_step_only":
      verdict["rationale"] += " Scope: the decision step on typed fields only; the extraction step that produces those fields stays probabilistic."
  if name == "DETERMINISTIC_CORE_FALLBACK":
    verdict["fallback_region"] = fit.get("fallback", {}).get("region") or "the region where `fallback` in Rule.lean is true"
  if name == "INCONCLUSIVE" and ran and receipt["lower_bound"] < BOUND_DETERMINISTIC: verdict["rationale"] += weak_note(fit)
  if name == "INCONCLUSIVE" and scan.get("near") and ran:
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
  if not halves: return {"known": False, "note": "no token count in the log lines; add `tokens` (or `usage`) per case to get token use and savings"}
  n, total, name = sum(t["with_tokens"] for t in halves.values()), sum(t["total"] for t in halves.values()), verdict["verdict"]
  if name in ("DETERMINISTIC", "CONSTANT"): share, why = 1.0, f"{name}: the rule takes over every run"
  elif name == "DETERMINISTIC_CORE_FALLBACK":
    outside, held = receipt["fallback"]["outside"], receipt["n_heldout"]
    share, why = round(outside / held, 4), f"{outside} of {held} held-back cases lie outside the fallback region; the rest stays with the LLM"
  else: share, why = 0.0, f"{name}: no rule replaces the loop"
  mean = round(total / n, 1)
  block = {"known": True, "per_run": mean, "logged": total, "cases_with_tokens": n, "halves": halves, "rule_share": share,
           "saved_per_run": round(share * mean, 1), "saved_logged": round(share * total)}
  block["arithmetic"] = (f"{total} tokens / {n} runs with a count ({' + '.join(halves)} half) = {mean} per run; rule share {share} ({why}); saved "
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
  stats.write_text_atomic(Path(out), json.dumps(verdict, indent=2))
  return verdict

def conclude(scope: str | None = None, note: str | None = None) -> int:
  # write verdict.json and print how the procedure ended (VERDICT, WHY, NEXT); returns the exit code, 1 when no verdict could be written
  result = write(scope, note)
  if "error" in result:
    needs_scope = result.get("needs") == "scope"  # the replay ran before the scope was settled
    print(f"NEXT: `{RUN_COMMAND} --scope decision_step_only` or `{RUN_COMMAND} --scope full`" if needs_scope else "NEXT: see WHY")
    stats.show("WHY: " + result["error"])
    return 0 if needs_scope else 1
  stats.emit({"written": "verdict.json", **result})
  print(f"VERDICT: {result['verdict']}" + (f" (scope: {result['scope']})" if "scope" in result else ""))
  stats.show("WHY: " + result["rationale"])
  print(f"TOKENS: {result['tokens'].get('arithmetic', 'unknown')}" + (f" ({result['tokens']['note']})" if "note" in result["tokens"] else ""))
  print("NEXT: nothing to run; verdict.json is written. Report it as it is." + (
    " A person confirms that Rule.lean matches the written policy before it replaces the loop." if "scope" in result else ""))
  return 0

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="The verdict as code: compute it from the recorded numbers and write verdict.json.")
  parser.add_argument("--scope", choices=("full", "decision_step_only"), default=None, help="override the scope recorded by audit.py")
  parser.add_argument("--note", default=None, help="your own remark, appended to the rationale")
  args = parser.parse_args()
  verdict = write(args.scope, args.note)
  stats.emit(verdict if "error" in verdict else {"written": "verdict.json", **verdict})
  sys.exit(1 if "error" in verdict else 0)
