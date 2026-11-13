# for a host that keeps the held-back half away from the agent: answer a replay request and write the verdict
# usage: python3 scripts/host_replay.py --heldout FILE [--workdir DIR] [--receipts DIR] [--types FILE] [--schema FILE] [--early FILE]
#                                       [--rule Rule.lean] [--replay-only]
# NOTE: the exam is only worth something if the rule never saw the held-back cases, and the surest way is that the agent cannot read
# them. The agent's run.py then stops before the exam and leaves work/replay_request.json, which names the Rule.lean it wants examined
# by its hash. This script refuses when nothing is requested or Rule.lean is no longer the requested rule (neither uses up the replay),
# runs the exam once, keeps the receipt in --receipts (a directory the agent cannot write, if you have one) with a copy in the agent's
# work/, then writes verdict.json in the agent's directory and prints the `VERDICT:` line. --replay-only serves an agent that works
# without run.py: no request is needed, no verdict is written, and the replay report is printed as one JSON line.
from __future__ import annotations
import argparse, os, shutil, sys
from pathlib import Path
import lean, replay, stats, verdict

def refuse(reason: str):
  stats.emit({"error": f"{reason} (replay not used)"})
  sys.exit(1)

def main():
  lean.require_lean()
  parser = argparse.ArgumentParser(description="Answer a replay request for a host that keeps the held-back half, and write the verdict.")
  parser.add_argument("--heldout", required=True, type=Path, help="the held-back half, which the agent cannot read")
  parser.add_argument("--workdir", default=".", type=Path, help="the agent's working directory (default: the current one)")
  parser.add_argument("--receipts", default=None, type=Path, help="where the receipt of the one replay is kept (default: the agent's work/)")
  for name in ("types", "schema", "early"): parser.add_argument(f"--{name}", default=None, type=Path, help="default: the agent's own file")
  parser.add_argument("--rule", default="Rule.lean", help="relative to the agent's directory")
  parser.add_argument("--replay-only", action="store_true", help="run the replay without a request and without writing the verdict")
  args = parser.parse_args()

  here = Path.cwd()
  heldout, receipts = here / args.heldout, here / args.receipts if args.receipts else None
  types, schema, early = (here / given if given else Path(default)
                          for given, default in ((args.types, "Types.lean"), (args.schema, "schema.json"), (args.early, "logs/early.jsonl")))
  os.chdir(here / args.workdir)  # NOTE: verdict.py and the receipt copy work in the agent's directory
  rule, request = Path(args.rule), stats.WORK_DIR / stats.REQUEST_FILE
  if not rule.is_file(): refuse(f"{args.rule} not found")
  rule_src = rule.read_text(encoding="utf-8", errors="replace")
  if not args.replay_only:
    asked = stats.read_json(request)
    if not asked: refuse(f"no replay is requested: {request} is missing")
    if asked.get("rule_sha256") != stats.sha256(rule_src): refuse(f"{args.rule} is not the rule the request names; run.py has to request it again")

  report = replay.replay_once(types.read_text(encoding="utf-8"), rule_src, stats.read_schema(schema), stats.read_jsonl(early),
                              stats.read_jsonl(heldout), stats.sha256(heldout.read_bytes()), receipts or stats.WORK_DIR)
  if receipts and (receipts / stats.RECEIPT_FILE).exists():  # the agent's scripts resume from their own copy
    stats.WORK_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(receipts / stats.RECEIPT_FILE, stats.WORK_DIR / stats.RECEIPT_FILE)
  stats.emit(report, trim=("counterexamples",))
  if args.replay_only or replay.read_receipt() is None: sys.exit(1 if "error" in report else 0)
  sys.exit(verdict.conclude())

if __name__ == "__main__": main()
