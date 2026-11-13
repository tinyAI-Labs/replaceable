# the one-shot held-out exam: run Rule.lean once on the held-back half and print the agreement statistics
# usage: python3 scripts/replay.py [Rule.lean]   (in the working directory: logs/heldout.jsonl, logs/early.jsonl, Types.lean, schema.json)
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any, Sequence
import lean, stats

RECEIPT_RESULT = ("rule_sha256", "n_heldout", "n_match", "agreement", "lower_bound", "baseline", "all_outputs_constant")
USED = {"error": "replay already used: the first result stands"}

def replay_report(types_src: str, rule_src: str, schema: stats.Schema, early: Sequence[stats.Row], heldout: Sequence[stats.Row], workdir: Path,
                  **lean_args: Any) -> dict[str, Any]:
  # agreement of `f` with the held-out outputs: counts, 95% lower bound, baseline, fallback containment; {"failure": code} if it did not run
  run = lean.run_rule(types_src, rule_src, schema, [r["typed_input"] for r in heldout], workdir, **lean_args)
  if run.outputs is None: return {"failure": run.failure}
  n, k, held_outputs = len(heldout), sum(p == r["output"] for p, r in zip(run.outputs, heldout)), [r["output"] for r in heldout]
  constant = len({r["output"] for r in early} | set(held_outputs)) == 1
  base_cls, base = stats.baseline(held_outputs, schema["output"]["values"])
  report = {"n_heldout": n, "n_match": k, "agreement": round(k / n, 4), "lower_bound": round(stats.clopper_pearson_lower(k, n), 4),
            "baseline": round(base, 4), "baseline_class": base_cls, "all_outputs_constant": constant,
            "counterexamples": stats.misses(heldout, run.outputs, 3)}
  if run.fallback is not None: report["fallback"] = stats.fallback_stats(heldout, run.outputs, run.fallback)
  if stats.token_stats(heldout)["with_tokens"]: report["tokens"] = stats.token_stats(heldout)  # NOTE: aggregates only, like everything in the receipt
  return report

def replay_once(types_src: str, rule_src: str, schema: stats.Schema, early: Sequence[stats.Row], heldout: Sequence[stats.Row], heldout_sha256: str,
                workdir: Path, **lean_args: Any) -> dict[str, Any]:
  # NOTE: the receipt is written before the rule runs, so a second call is refused whatever happened in the first
  receipt_path = workdir / stats.RECEIPT_FILE
  if receipt_path.exists(): return {**USED, "receipt": read_receipt(workdir)}
  check = lean.check_rule(types_src, rule_src, workdir, **lean_args)
  if not check.compiled: return {"error": "the rule must pass the check before the replay (replay not used)", "violations": list(check.violations)}
  receipt = {"rule_sha256": stats.sha256(rule_src), "heldout_sha256": heldout_sha256}
  workdir.mkdir(parents=True, exist_ok=True)
  try:  # NOTE: creating the file is atomic, so two replays cannot both run; `replay_interrupted` is what stands if this process dies
    with receipt_path.open("x", encoding="utf-8") as stream: json.dump({**receipt, "failure": "replay_interrupted"}, stream, indent=2)
  except FileExistsError: return {**USED, "receipt": read_receipt(workdir)}
  report = replay_report(types_src, rule_src, schema, early, heldout, workdir, **lean_args)
  stats.write_text_atomic(receipt_path, json.dumps({**receipt, **{k: v for k, v in report.items() if k != "counterexamples"}}, indent=2))
  return {"error": report["failure"] + " (replay used)"} if "failure" in report else report

def read_receipt(workdir: Path = stats.WORK_DIR) -> dict[str, Any] | None:
  # NOTE: None if no replay ran. A receipt that cannot be read, or that holds no result, reads as a failed replay: the one exam is used
  # and its evidence is missing, which the verdict table turns into INCONCLUSIVE.
  path = workdir / stats.RECEIPT_FILE
  if not path.exists(): return None
  try: receipt = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, ValueError): receipt = None
  if not isinstance(receipt, dict): return {"failure": "receipt_unreadable"}
  if "failure" not in receipt and any(key not in receipt for key in RECEIPT_RESULT): return {**receipt, "failure": "replay_interrupted"}
  return receipt

def replay(rule="Rule.lean", heldout="logs/heldout.jsonl", early="logs/early.jsonl", types="Types.lean", schema="schema.json") -> dict[str, Any]:
  held = Path(heldout)
  if not held.is_file(): return {"error": f"held-out log {heldout} not found (replay not used)"}
  lean.require_lean()
  return replay_once(Path(types).read_text(encoding="utf-8"), Path(rule).read_text(encoding="utf-8"), stats.read_schema(schema),
                     stats.read_jsonl(early), stats.read_jsonl(held), stats.sha256(held.read_bytes()), stats.WORK_DIR)

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="The one-shot held-out exam: run Rule.lean on the held-back half and print agreement statistics.")
  parser.add_argument("rule", nargs="?", default="Rule.lean")
  report = replay(**vars(parser.parse_args()))
  stats.emit(report, trim=("counterexamples",))
  sys.exit(1 if "error" in report else 0)
