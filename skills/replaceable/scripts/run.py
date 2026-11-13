# the whole procedure in one command: the audit of the early half, the one-shot held-out replay, the verdict
# usage: python3 scripts/run.py [--scope full|decision_step_only] [--derive "x.account_no % 10"]... [--refit] [--note "..."]
# NOTE: it runs every step itself and ends with a `VERDICT:` line. A weak rule is not a question: it takes the exam as it is and ends
# INCONCLUSIVE. Before the verdict it stops only where it cannot go on alone, with the exact command to continue (NEXT) and the reason
# (WHY): the scope is undeclared; a --derive expression is not valid Lean; a hand-edited Rule.lean does not pass the check; or the host
# keeps the held-back half, in which case the request is left in work/replay_request.json for host_replay.py. --derive has to come with
# the first call: the exam is taken in the same run. Run again, it resumes from work/audit.json and work/receipt.json.
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import audit, lean, replay, stats, verdict

RULE, HELDOUT, EARLY = Path("Rule.lean"), Path("logs/heldout.jsonl"), Path("logs/early.jsonl")
REQUEST = stats.WORK_DIR / stats.REQUEST_FILE

def stop(next_: str, why: str, code: int = 0):
  print("NEXT: " + next_)
  stats.show("WHY: " + why)
  sys.exit(code)

def current_audit(args: argparse.Namespace) -> dict:
  # the saved audit if it still describes the log, the rule and the answers given; otherwise a fresh one
  state = stats.read_json(audit.AUDIT_FILE)
  fit = state.get("fit")
  fresh = ("step" in state and state.get("early_sha256") == stats.sha256(EARLY.read_bytes())
           and (fit is None or (RULE.is_file() and stats.sha256(RULE.read_text(encoding="utf-8")) == fit["rule_sha256"])))
  if fresh and not (args.scope or args.derive or args.refit): return state
  return audit.audit(scope=args.scope, derive=args.derive, keep_rule=False if args.refit else None)

def main():
  lean.require_lean()
  parser = argparse.ArgumentParser(description="The whole procedure in one command: the audit of the early half, the one-shot replay, the verdict.")
  parser.add_argument("--scope", choices=("full", "decision_step_only"), default=None,
                      help="decision_step_only when the typed inputs were extracted from documents, free text or tool results")
  parser.add_argument("--derive", action="append", default=None, metavar="LEAN_EXPR",
                      help='a quantity you know the policy uses, as a Nat-valued Lean expression of x, e.g. "x.account_no %% 10"; repeatable')
  parser.add_argument("--refit", action="store_true", help="discard a hand-written Rule.lean and use the fitted rule")
  parser.add_argument("--note", default=None, help="your own remark, appended to the verdict's rationale")
  args = parser.parse_args()

  if replay.read_receipt() is None:  # before the exam
    if not EARLY.is_file():
      stop(f"`python3 {audit.scripts_dir()}/prepare.py <raw log>.jsonl`, then `{audit.RUN_COMMAND}`", "logs/early.jsonl is missing: the working "
           "directory needs schema.json, Types.lean and the split log, which prepare.py makes from one raw log.", 1)
    state = current_audit(args)
    stats.emit(audit.view(state), trim=("residual",))
    rejected = state.get("derive_rejected")
    if rejected and args.derive:  # NOTE: the exam is not taken on a rule the reader meant to improve
      stop(f'`{audit.RUN_COMMAND} --derive "<corrected expression>"`, or go on without it: `{audit.RUN_COMMAND}`',
           f"Lean rejected {rejected['expressions']}: {rejected['error'][:400]}. Nothing was fitted with it and no --derive run was used.", 1)
    step = audit.next_step(state)
    if step["action"] in ("scope", "fix_rule"):
      print("NEXT: " + audit.next_line(step, via_run=True))
      stats.show("WHY: " + step["why"])
      if step["action"] == "fix_rule": stats.show(f"--- {RULE} ---\n" + RULE.read_text(encoding="utf-8").rstrip())
      sys.exit(1 if step["action"] == "fix_rule" else 0)
    # NOTE: when the host keeps the held-back half, name the rule that is ready for the exam and leave the replay to the host
    if step["action"] == "replay" and not HELDOUT.is_file():
      stats.write_text_atomic(REQUEST, json.dumps({"rule": str(RULE), "rule_sha256": stats.sha256(RULE.read_text(encoding="utf-8"))}, indent=2))
      stop(f"nothing to run if a `VERDICT:` line follows below (the host has run the replay); otherwise `{audit.RUN_COMMAND}` again once it has",
           f"{step['why']} {HELDOUT} is not in this directory: the host keeps the held-back half. The replay of this rule is requested in "
           f"{REQUEST}; the host answers it with host_replay.py, which runs the exam once and writes the verdict.")
    if step["action"] == "replay":
      report = replay.replay()
      stats.emit(report, trim=("counterexamples",))
      if replay.read_receipt() is None:  # refused without using the replay
        stop(audit.next_line({"action": "fix_rule"}, via_run=True), f"The replay did not run: {report.get('error')}.", 1)
  sys.exit(verdict.conclude(args.scope, args.note))

if __name__ == "__main__": main()
