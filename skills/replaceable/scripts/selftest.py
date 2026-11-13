# installation check: run the whole procedure on generated loops whose right answer is known
# usage: python3 scripts/selftest.py [--keep]
# NOTE: each loop gets a temporary directory, a generated raw log, and then exactly the commands SKILL.md lists: prepare.py, then run.py
# (with the quantity the loop's description names, where it names one), answering the scope question when it is asked. One loop is run
# the way a host runs it that keeps the held-back half away from the agent. Exits 0 only if every verdict is the expected one.
from __future__ import annotations
import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path
from random import Random
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent
Draw = Callable[[Random], tuple[dict[str, Any], Any]]

def _pick(rng: Random, values: tuple) -> Any: return values[int(rng.random() * len(values))]

def shipping(rng: Random):
  # deterministic: weight and distance thresholds, a flag and a zone
  x = {"weight_g": int(rng.random() * 60_000), "distance_km": int(rng.random() * 2_000), "express": rng.random() < 0.3,
       "zone": _pick(rng, ("domestic", "eu", "world"))}
  if x["weight_g"] > 30_000: return x, "freight"
  return x, "surcharge" if x["zone"] == "world" or (x["express"] and x["distance_km"] >= 500) else "standard"

def ticket(rng: Random):
  # probabilistic: the same ticket type is routed differently from case to case
  x = {"product": _pick(rng, ("app", "card", "loan")), "urgent": rng.random() < 0.5}
  return x, "tier2" if rng.random() < (0.6 if x["urgent"] else 0.4) else "tier1"

def summary(rng: Random):
  # out of scope: free-text output
  x = {"topic": _pick(rng, ("fees", "outage", "fraud"))}
  return x, f"Customer wrote about {x['topic']}; reference {int(rng.random() * 1_000_000)}."

def refund(rng: Random):
  # core plus fallback: a threshold per reason, except damaged goods between 50 and 200 euros, which vary
  x = {"reason": _pick(rng, ("late", "damaged", "unwanted")), "amount_cents": int(rng.random() * 40_000)}
  if x["reason"] == "damaged" and 5_000 <= x["amount_cents"] < 20_000: return x, "manual" if rng.random() < 0.65 else "refund"
  return x, "refund" if x["amount_cents"] < {"late": 10_000, "damaged": 5_000, "unwanted": 2_500}[x["reason"]] else "manual"

def order(rng: Random):
  # deterministic on the order total, units times unit price: the built-in search for a derived quantity has to find it
  x = {"units": 1 + int(rng.random() * 400), "unit_price_cents": 100 + int(rng.random() * 50_000), "express": rng.random() < 0.3}
  total = x["units"] * x["unit_price_cents"]
  return x, "director" if total > 4_000_000 else ("manager" if total > 500_000 else "self_serve")

def shard(rng: Random):
  # deterministic, but on a quantity no search tries (odd or even ticket number): it has to be offered with --derive
  x = {"ticket_no": int(rng.random() * 1_000_000), "vip": rng.random() < 0.2}
  return x, "desk_vip" if x["vip"] else ("desk_a" if x["ticket_no"] % 2 == 0 else "desk_b")

def claim(rng: Random):
  # deterministic on a log as real systems write it: decimals, a negative amount, nulls, values and names that are not Lean identifiers,
  # a true/false output, an identifier column and the source text next to the fields
  x = {"amount": round(rng.random() * 450 - 50, 2), "type": _pick(rng, ("gold-plus", "2nd tier", "basic", None)),
       "score": None if rng.random() < 0.2 else int(rng.random() * 100), "customer id": f"C{int(rng.random() * 10**9)}"}
  return x, x["amount"] >= 0 and x["score"] is not None and (x["amount"] < 120 or x["type"] == "gold-plus")

# name, generator, cases, expected verdict, how to run it: prepare.py options, the scope, a derived quantity, a host that keeps the held-back half
LOOPS: tuple[tuple[str, Draw, int, str, dict[str, Any]], ...] = (
  ("shipping", shipping, 600, "DETERMINISTIC", {"host": True}),
  ("ticket", ticket, 400, "PROBABILISTIC", {}),
  ("summary", summary, 300, "OUT_OF_SCOPE", {}),
  ("refund", refund, 800, "DETERMINISTIC_CORE_FALLBACK", {}),
  ("shipping_small", shipping, 100, "INCONCLUSIVE", {}),
  ("order", order, 600, "DETERMINISTIC", {}),
  ("shard", shard, 600, "DETERMINISTIC", {"derive": "x.ticket_no % 2"}),
  ("claim", claim, 600, "DETERMINISTIC", {"prepare": ("--drop", "customer id"), "source": True, "scope": "decision_step_only"}),
  ("shipping_tokens", shipping, 600, "DETERMINISTIC", {"tokens": True}),  # the same loop with a token count per run: same verdict, plus savings
)
def usage(i: int) -> dict[str, Any]: return {"usage": {"input_tokens": 800 + i % 50, "output_tokens": 40}}  # what an SDK logs per call
TOKENS_600 = sum(840 + i % 50 for i in range(600))  # NOTE: the rule takes over every run of a DETERMINISTIC loop, so all of it is saved

def _run(script: str, *args: str, cwd: Path) -> str:
  done = subprocess.run([sys.executable, str(SCRIPTS / script), *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
  return done.stdout + done.stderr

def run_loop(name: str, draw: Draw, n: int, answers: dict[str, Any], root: Path) -> dict[str, Any]:
  work, rng = root / name, Random(20261111)
  work.mkdir()
  with (work / "raw.jsonl").open("w", encoding="utf-8") as stream:
    for i in range(n):
      typed, output = draw(rng)
      source = {"form_text": f"Claim form {i}, as received"} if answers.get("source") else {}
      if answers.get("tokens"): source.update(usage(i))
      stream.write(json.dumps({"case_id": f"c{i:04d}", "ts": i, **source, "typed_input": typed, "output": output}) + "\n")
  said = _run("prepare.py", "raw.jsonl", *answers.get("prepare", ()), cwd=work)
  safe = root / f"{name}_host"  # what a host keeps out of the agent's reach: the held-back half and the receipt
  if answers.get("host"):
    safe.mkdir()
    (work / "logs" / "heldout.jsonl").rename(safe / "heldout.jsonl")
  said += _run("run.py", *(("--derive", answers["derive"]) if "derive" in answers else ()), cwd=work)
  if "--scope" in said.split("NEXT:")[-1].splitlines()[0] and "scope" in answers: said += _run("run.py", "--scope", answers["scope"], cwd=work)
  if answers.get("host"):  # run.py has left work/replay_request.json
    said += _run("host_replay.py", "--heldout", str(safe / "heldout.jsonl"), "--workdir", str(work), "--receipts", str(safe), cwd=root)
  try: return json.loads((work / "verdict.json").read_text(encoding="utf-8"))
  except (OSError, ValueError): return {"verdict": "NO_VERDICT", "rationale": said[-600:]}

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Installation check: run the whole procedure on generated loops whose right answer is known.")
  parser.add_argument("--keep", action="store_true", help="keep the temporary directories and print where they are")
  args = parser.parse_args()
  root, failed = Path(tempfile.mkdtemp(prefix="replaceable_")), 0
  for name, draw, n, expected, answers in LOOPS:
    verdict = run_loop(name, draw, n, answers, root)
    ok = verdict["verdict"] == expected and verdict.get("tokens", {}).get("known") == bool(answers.get("tokens"))
    if ok and answers.get("tokens"): ok = [verdict["tokens"][k] for k in ("logged", "saved_logged", "rule_share")] == [TOKENS_600, TOKENS_600, 1.0]
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name:15} expected {expected:28} got {verdict['verdict']:28} {verdict.get('rationale', '')[:110]}")
  if args.keep: print(f"kept: {root}")
  else: shutil.rmtree(root, ignore_errors=True)
  print("selftest passed" if not failed else f"selftest FAILED ({failed} of {len(LOOPS)})")
  sys.exit(1 if failed else 0)
