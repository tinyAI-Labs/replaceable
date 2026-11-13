# statistics, the thresholds of the procedure and the small file helpers every script shares (standard library only)
from __future__ import annotations
import hashlib, json, os, sys
from collections import Counter
from math import ceil, exp, lgamma, log, log1p
from pathlib import Path
from statistics import median
from typing import Any, Sequence

if sys.version_info < (3, 12): raise SystemExit("these scripts need Python 3.12 or newer (this is %d.%d)" % sys.version_info[:2])

Row = dict[str, Any]  # one logged case: {case_id, ts, typed_input, output, tokens?}
Schema = dict[str, Any]  # schema.json: {"input": [{name, type, values?, role?}], "output": {name, values}}

MAX_OUTPUT = 2048  # characters a script may print
WORK_DIR = Path("work")  # scratch files and the replay receipt, relative to the working directory
RECEIPT_FILE = "receipt.json"
REQUEST_FILE = "replay_request.json"  # left in WORK_DIR by run.py when the host keeps the held-back half; host_replay.py answers it

# the decision thresholds of the procedure (SKILL.md states the same numbers)
MIN_TOTAL = 150  # logged cases needed before anything is tested
MIN_HELDOUT = 60  # held-back cases needed for a replay verdict (and outside a fallback region)
CONFLICT_THRESHOLD = 0.05  # share of the repeated-input cases that disagree with the majority of their identical-input group
MIN_CONFLICT_GROUPS = 2  # distinct inputs that must show a conflict before the loop is called PROBABILISTIC
MIN_REPEAT_SHARE = 0.2  # below this almost every input is unique and the conflict scan cannot see anything
BOUND_DETERMINISTIC = 0.95  # 95% lower bound on held-out agreement for DETERMINISTIC and CONSTANT
BOUND_CORE = 0.80  # ... for DETERMINISTIC_CORE_FALLBACK
BASELINE_ERROR_RATIO = 0.5  # the rule's error bound (1 - L) may be at most this share of the error of the most common output

def sha256(data: str | bytes) -> str: return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
def read_jsonl(path) -> list[Row]: return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
def read_schema(path) -> Schema: return json.loads(Path(path).read_text(encoding="utf-8"))
def decision_fields(schema: Schema) -> list[str]: return [f["name"] for f in schema["input"] if f.get("role", "decision") == "decision"]
# tie-break order for majority outputs: the schema's values, else the sorted observed outputs
def output_order(schema: Schema, rows: Sequence[Row]) -> list[str]: return list(schema["output"]["values"]) or sorted({r["output"] for r in rows})

def read_json(path) -> dict[str, Any]:
  try: found = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError): return {}
  return found if isinstance(found, dict) else {}

def read_early(path, heldout="logs/heldout.jsonl") -> list[Row]:
  # NOTE: refuses the held-back half (same file, same name or a byte-identical copy). A guard against mistakes, not against intent:
  # only a host that withholds the held-back half can enforce it.
  log_, held = Path(path), Path(heldout)
  same = log_.name == held.name or (held.is_file() and log_.is_file() and (
    log_.samefile(held) or (log_.stat().st_size == held.stat().st_size and sha256(log_.read_bytes()) == sha256(held.read_bytes()))))
  if same:
    emit({"error": f"{path} is the held-back half: only replay.py reads it, once. Rules are fitted and inspected on logs/early.jsonl."})
    raise SystemExit(2)
  return read_jsonl(log_)

def write_text_atomic(path: Path, text: str):
  # a reader sees the old content or the new, never half of it
  scratch = path.with_name(path.name + ".tmp")
  scratch.write_text(text, encoding="utf-8")
  os.replace(scratch, path)

def emit(report: dict[str, Any], trim: Sequence[str] = ()):
  # one JSON line of at most MAX_OUTPUT characters, shortening the lists named in `trim`
  text = json.dumps(report)
  while len(text) > MAX_OUTPUT:
    key = max(trim, key=lambda k: len(report.get(k) or ()), default=None)
    if key is None or not report.get(key): break
    report[key], report["truncated"] = report[key][:-1], True
    text = json.dumps(report)
  print(text)

def show(text: str):
  # text that may hold non-ASCII characters (a hand-written rule, a --derive expression) on any terminal encoding
  encoding = sys.stdout.encoding or "ascii"
  print(text.encode(encoding, errors="backslashreplace").decode(encoding))

# *** tokens per loop run ***
# NOTE: tokens are bookkeeping for the savings estimate only. They are read from what the log states, never estimated, and never reach
# the typed inputs, the fit or the verdict table. Contract: one log line is one loop run, and its count is the total of that run.
TOKEN_PARTS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "prompt_tokens", "completion_tokens")
TOKEN_KEYS = {"tokens", "total_tokens", "usage", *TOKEN_PARTS}  # log keys that are neither typed inputs nor source material

def _is_count(v: Any) -> bool: return isinstance(v, int) and not isinstance(v, bool) and v >= 0

def row_tokens(row: Row) -> int | None:
  # `tokens` or `total_tokens`, else the sum of the input/output parts; at the top level of the line, else under `usage`. None when absent.
  for src in (row, row.get("usage")):
    if not isinstance(src, dict): continue
    for key in ("tokens", "total_tokens"):
      if _is_count(src.get(key)): return src[key]
    parts = [src[key] for key in TOKEN_PARTS if _is_count(src.get(key))]
    if parts: return sum(parts)
  return None

def token_stats(rows: Sequence[Row]) -> dict[str, Any]:
  # aggregates only (this is all the replay returns about the held-back half): p90 is the nearest-rank percentile
  counts = sorted(t for t in map(row_tokens, rows) if t is not None)
  if not counts: return {"cases": len(rows), "with_tokens": 0}
  return {"cases": len(rows), "with_tokens": len(counts), "total": sum(counts), "mean": round(sum(counts) / len(counts), 1),
          "median": median(counts), "p90": counts[ceil(0.9 * len(counts)) - 1]}

# *** statistics ***

def _binom_sf(k: int, n: int, p: float) -> float:
  # P(X >= k) for X ~ Binomial(n, p), summed in log space so that large n neither overflows nor underflows
  if p <= 0.0: return 1.0 if k <= 0 else 0.0
  if p >= 1.0: return 1.0
  log_p, log_q, log_n = log(p), log1p(-p), lgamma(n + 1)
  terms = [log_n - lgamma(i + 1) - lgamma(n - i + 1) + i * log_p + (n - i) * log_q for i in range(max(k, 0), n + 1)]
  top = max(terms)
  return min(1.0, exp(top) * sum(exp(t - top) for t in terms))

def clopper_pearson_lower(k: int, n: int, conf: float = 0.95) -> float:
  # one-sided exact lower bound on a success probability after k successes in n trials (bisection on the binomial tail)
  if n <= 0 or k <= 0: return 0.0
  lo, hi = 0.0, 1.0
  for _ in range(60):
    mid = (lo + hi) / 2
    if _binom_sf(k, n, mid) < 1 - conf: lo = mid
    else: hi = mid
  return lo

def majority_class(outputs: Sequence[str], order: Sequence[str]) -> str:
  counts = Counter(outputs)
  return max(order, key=lambda v: (counts[v], -order.index(v)))  # ties break by `order`

def baseline(heldout_outputs: Sequence[str], order: Sequence[str]) -> tuple[str, float]:
  # the most common held-out output and its share: what the best constant answer would score on the exam
  cls = majority_class(heldout_outputs, order)
  return cls, sum(o == cls for o in heldout_outputs) / len(heldout_outputs)

# NOTE: the test is relative (error bound at most BASELINE_ERROR_RATIO of the constant's error), so a log with a 96% majority can still
# pass (it needs L >= 0.98), which a fixed margin in points would forbid
def beats_baseline(lower_bound: float, base: float) -> bool: return 1 - lower_bound <= BASELINE_ERROR_RATIO * (1 - base) + 1e-9

def conflict_scan(rows: Sequence[Row], fields: Sequence[str], order: Sequence[str], max_groups: int | None = 20) -> dict[str, Any]:
  # NOTE: conflict_rate counts every non-majority output inside a group of identical decision inputs, divided by the rows whose input
  # occurs more than once (unique inputs cannot conflict, so they must not dilute it). repeat_share near zero: the scan says nothing.
  groups: dict[tuple, list[Row]] = {}
  for row in rows: groups.setdefault(tuple(row["typed_input"][f] for f in fields), []).append(row)
  summaries, non_majority = [], 0
  for key, members in groups.items():
    counts = Counter(m["output"] for m in members)
    majority = majority_class([m["output"] for m in members], order)
    non_majority += len(members) - counts[majority]
    if len(members) < 2: continue
    other = next((m for m in members if m["output"] != members[0]["output"]), None)
    pair = [members[0]["case_id"], other["case_id"]] if other else None
    summaries.append({"input": dict(zip(fields, key)), "n": len(members), "counts": dict(counts), "majority": majority,
                      "majority_share": round(counts[majority] / len(members), 4), "pair": pair})
  summaries.sort(key=lambda g: -g["n"])
  pairs = [g.pop("pair") for g in summaries]
  repeated = sum(g["n"] for g in summaries)
  return {"n_early": len(rows), "n_groups": len(groups), "repeat_share": round(repeated / len(rows), 4) if rows else 0.0,
          "conflict_rate": round(non_majority / repeated, 4) if repeated else 0.0, "conflicted_groups": sum(p is not None for p in pairs),
          "groups": summaries if max_groups is None else summaries[:max_groups], "example_pairs": [p for p in pairs if p][:3]}

def near_conflicts(rows: Sequence[Row], schema: Schema, max_rows: int = 1500) -> dict[str, Any]:
  # NOTE: the conflict scan for inputs that never repeat. Each case is compared with its nearest neighbour: same enum and bool values,
  # closest in the numbers (as ranks, so units do not matter). `chance` is the disagreement of random pairs with the same categories.
  # A rule with a few thresholds sits far below chance; noise or a hidden input pushes it towards chance. A diagnostic, never a verdict.
  rows = list(rows[:max_rows])
  fields = [f for f in schema["input"] if f["name"] in decision_fields(schema)]
  ranks = []
  for name in [f["name"] for f in fields if f["type"] == "nat"]:
    ordered = sorted({r["typed_input"][name] for r in rows})
    position = {v: i / max(len(ordered) - 1, 1) for i, v in enumerate(ordered)}
    ranks.append([position[r["typed_input"][name]] for r in rows])
  cells: dict[tuple, list[int]] = {}
  for i, r in enumerate(rows): cells.setdefault(tuple(r["typed_input"][f["name"]] for f in fields if f["type"] != "nat"), []).append(i)
  compared, differ, chance, pairs = 0, 0, 0.0, []
  for members in cells.values():
    if len(members) < 2: continue
    counts = Counter(rows[i]["output"] for i in members)
    chance += len(members) * (1 - sum((c / len(members)) ** 2 for c in counts.values()))
    for i in members:
      dist, j = min((sum((col[i] - col[j]) ** 2 for col in ranks), j) for j in members if j != i)
      compared += 1
      if rows[i]["output"] != rows[j]["output"]:
        differ += 1
        pairs.append((dist, [rows[i]["case_id"], rows[j]["case_id"]]))
  return {"compared": compared, "neighbour_disagreement": round(differ / compared, 4) if compared else None,
          "chance": round(chance / compared, 4) if compared else None, "closest_differing_pairs": [pair for _, pair in sorted(pairs)[:3]]}

def conflict_reading(scan: dict[str, Any]) -> tuple[str, str]:
  # (`conflicts` | `one_group` | `noise` | `uninformative`, one sentence)
  if scan["repeat_share"] < MIN_REPEAT_SHARE:
    return "uninformative", "Inputs almost never repeat (groups ~ cases), so this scan shows nothing either way. Continue to the rule."
  if scan["conflict_rate"] > CONFLICT_THRESHOLD and scan["conflicted_groups"] >= MIN_CONFLICT_GROUPS:
    return "conflicts", (f"Identical inputs got different outputs on over {CONFLICT_THRESHOLD:.0%} of the repeated cases, "
                         f"for {scan['conflicted_groups']} distinct inputs.")
  if scan["conflict_rate"] > CONFLICT_THRESHOLD:
    return "one_group", (f"Over {CONFLICT_THRESHOLD:.0%} of the repeated cases conflict, but all for one input: a single group is not enough "
                         "to call the loop probabilistic. Continue to the rule.")
  return "noise", f"Repeated inputs agree (conflicts at or under {CONFLICT_THRESHOLD:.0%}). Continue to the rule."

def fallback_stats(rows: Sequence[Row], predicted: Sequence[str], flags: Sequence[bool]) -> dict[str, Any]:
  # containment counts for a core-plus-fallback rule: mismatches must sit inside the declared region
  outside = [(p, r) for p, r, inside in zip(predicted, rows, flags) if not inside]
  misses_outside = sum(p != r["output"] for p, r in outside)
  return {"inside": sum(flags), "outside": len(outside), "misses_outside": misses_outside,
          "outside_lower_bound": round(clopper_pearson_lower(len(outside) - misses_outside, len(outside)), 4)}

def misses(rows: Sequence[Row], predicted: Sequence[str], limit: int) -> list[dict[str, Any]]:
  wrong = [(p, r) for p, r in zip(predicted, rows) if p != r["output"]][:limit]
  return [{"case_id": r["case_id"], "typed_input": r["typed_input"], "expected": r["output"], "got": p} for p, r in wrong]
