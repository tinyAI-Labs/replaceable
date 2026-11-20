# turn one raw log into the layout the other scripts expect
# usage: python3 scripts/prepare.py raw_log.jsonl [--schema schema.json] [--extraction-step] [--drop FIELD]... [--max-rows N]
# NOTE: input is JSONL, one logged case per line, with the typed input under `typed_input` (or `input`) and the loop's answer under
# `output`; `ts` (any sortable timestamp), `case_id` and `correct` (true/false: was this run right, from a human override or a check) are
# optional. With `correct` on every line, the runs before the loop held 95% are set aside as its learning phase and the rest is the log (a loop that
# never holds 95%: its latest half). It writes logs/early.jsonl (the earliest half, which the rule is fitted on), logs/heldout.jsonl (the latest
# half: only the replay reads it, and a host should move it out of the agent's reach),
# schema.json (inferred unless --schema is given), Types.lean and metadata.json. How a field is typed (every change is listed in `notes`):
# - whole numbers become `nat`; decimals with up to MAX_DECIMALS places are scaled to whole numbers (12.50 -> 1250, `scale` in the schema);
#   a field with negative values is split into its magnitude and a `<name>_negative` flag
# - a date written `2024-03-21` (a time after it is dropped) becomes the number 20240321, so rules can compare dates
# - true/false becomes `bool`; strings with at most MAX_ENUM distinct values become `enum`, values that are not Lean identifiers are
#   rewritten (`in-review` -> `in_review`) with the originals under `labels`
# - a missing or null value becomes the enum value `missing`, or for a number 0 plus a `<name>_missing` flag
# - an output with more than MAX_ENUM distinct values is free text (output values = []), which the audit reports as OUT_OF_SCOPE
# Anything else (free text, identifiers, nested data, mixed types) stops the run: convert the field, or leave it out with --drop.
from __future__ import annotations
import argparse, json, math, re, shlex, sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
import lean, stats
from audit import RUN_COMMAND

MAX_ENUM, MAX_DECIMALS = 20, 6
LARGE_LOG = 20_000  # above this the fit and the Lean runs get slow; --max-rows is suggested
MISSING = "missing"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?")
Column = tuple[dict[str, Any], list[Any]]  # a schema field and its value for every row

def identifier(text: str, taken: set[str]) -> str:
  # a Lean identifier for a logged name or value, unique in `taken` (which it joins)
  name = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40]
  if not name or not name[0].isalpha() or name in lean.LEAN_RESERVED: name = f"v_{name}" if name else "v_empty"
  base, k = name, 2
  while name in taken: name, k = f"{base}_{k}", k + 1
  taken.add(name)
  return name

def field_namer() -> Callable[[str], str]:
  # names for input fields: identifiers whose Lean type names (`pascal`) are free and distinct
  taken: set[str] = set()
  def name(raw: str) -> str:
    found = identifier(raw, set())
    if lean.pascal(found) in lean.RESERVED_TYPES or re.fullmatch(r"derived\d+", found): found += "_field"
    base, k = found, 2
    while lean.pascal(found) in taken: found, k = f"{base}_{k}", k + 1
    taken.add(lean.pascal(found))
    return found
  return name

def as_date(value: Any) -> int | None:
  # 20240321 for the text `2024-03-21` (with or without a time after it), None for anything else
  if not isinstance(value, str) or not _ISO_DATE.fullmatch(value): return None
  try: day = date.fromisoformat(value[:10])
  except ValueError: return None
  return day.year * 10_000 + day.month * 100 + day.day

def as_enum(values: list[Any]) -> tuple[list[str], dict[str, str], dict[str, Any]]:
  # enum identifiers for raw values (strings, bools, numbers, null): (identifiers, JSON of raw -> identifier, identifier -> raw where they differ)
  taken = {MISSING} if any(v is None for v in values) else set()
  to_id: dict[str, str] = {}  # NOTE: keyed by the JSON text of the raw value, so true, 1 and "1" stay apart
  for key in sorted({json.dumps(v) for v in values}):
    raw = json.loads(key)
    to_id[key] = MISSING if raw is None else identifier(raw if isinstance(raw, str) else key, taken)
  return sorted(to_id.values()), to_id, {ident: json.loads(key) for key, ident in to_id.items() if json.loads(key) != ident}

def type_field(raw: str, values: list[Any], name: Callable[[str], str]) -> tuple[list[Column], list[str], str | None]:
  # type one raw input field: (schema fields with their columns, notes on what was changed, the problem if it cannot be typed)
  present = [v for v in values if v is not None]
  missing, drop = len(values) - len(present), f"leave it out with --drop {shlex.quote(raw)}"
  field: dict[str, Any] = {"name": name(raw), "role": "decision"}
  label, notes = field["name"], [f"{raw!r} is named {field['name']!r}"] if field["name"] != raw else []
  if label != raw: field["source"] = raw
  if present and all(isinstance(v, bool) for v in present) and not missing: return [({**field, "type": "bool"}, values)], notes, None
  if present and all(as_date(v) is not None for v in present):  # from here on a date is a number like any other
    values = [None if v is None else as_date(v) for v in values]
    present = [v for v in values if v is not None]
    field["unit"] = "calendar date as YYYYMMDD"
    notes.append(f"{label}: dates became numbers (2024-03-21 is 20240321); a time of day is dropped")
  types = {type(v) for v in present}
  if present and (len(types) == 1 and types <= {bool, str} or str in types and types <= {int, float, str}):  # 2, 3, "5more" is a category
    idents, to_id, labels = as_enum(values)
    if len(idents) > MAX_ENUM:
      return [], [], (f"input field {raw!r} has {len(idents)} distinct text values (more than {MAX_ENUM}): free text or an identifier. "
                      f"Convert it to a category, or {drop}")
    gone = f"; {missing} missing value(s) became `{MISSING}`" if missing else ""
    if labels: notes.append(f"{label}: values rewritten as identifiers, originals under `labels`{gone}")
    field = {**field, "type": "enum", "values": idents, **({"labels": labels} if labels else {})}
    return [(field, [to_id[json.dumps(v)] for v in values])], notes, None
  if present and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in present):
    exact = [Decimal(repr(v)) for v in present if isinstance(v, float)]  # the decimals as they were logged
    places = max(0, 0, *(-d.normalize().as_tuple().exponent for d in exact))
    if places > MAX_DECIMALS:
      return [], [], (f"input field {raw!r} has decimals with more than {MAX_DECIMALS} places: round it to the unit the policy uses "
                      f"(e.g. money to whole cents), or {drop}")
    numbers = [None if v is None else int(Decimal(repr(v)).scaleb(places)) if isinstance(v, float) else v * 10 ** places for v in values]
    if places:
      field["scale"] = 10 ** places
      notes.append(f"{label}: decimals scaled by {10 ** places} to whole numbers (a logged 12.5 is {int(Decimal('12.5').scaleb(places))})")
    columns: list[Column] = [({**field, "type": "nat"}, [abs(v or 0) for v in numbers])]
    if any(v is not None and v < 0 for v in numbers):
      flag = name(f"{label}_negative")
      columns.append(({"name": flag, "type": "bool", "role": "decision", "source": raw}, [v is not None and v < 0 for v in numbers]))
      notes.append(f"{label}: has negative values, so it holds the magnitude and `{flag}` the sign")
    if missing:
      flag = name(f"{label}_missing")
      columns.append(({"name": flag, "type": "bool", "role": "decision", "source": raw}, [v is None for v in numbers]))
      notes.append(f"{label}: {missing} missing value(s) became 0 with `{flag}` = true")
    return columns, notes, None
  kinds = "/".join(sorted({type(v).__name__ for v in values})) or "no values"
  return [], [], (f"input field {raw!r} ({kinds}) is not a number, a true/false or a text with at most {MAX_ENUM} values: "
                  f"convert it (nested data to separate fields, text to a category), or {drop}")

def infer(rows: list[stats.Row], drop: set[str]) -> tuple[stats.Schema, list[stats.Row], list[str], list[str]]:
  # a schema for the rows, the rows rewritten to fit it, notes on every change and the problems that stop it
  names = [key for key in dict.fromkeys(key for r in rows for key in r["typed_input"]) if key not in drop]
  namer, columns, notes, problems = field_namer(), [], [f"{sorted(drop)} left out (--drop)"] if drop else [], []
  for raw in names:
    found, said, problem = type_field(raw, [r["typed_input"].get(raw) for r in rows], namer)
    columns, notes, problems = columns + found, notes + said, problems + ([problem] if problem else [])
  outputs = [r["output"] for r in rows]
  scalar = all(v is None or isinstance(v, (bool, int, float, str)) for v in outputs)
  idents, to_id, labels = as_enum(outputs) if scalar and len({json.dumps(v) for v in outputs}) <= MAX_ENUM else ([], {}, {})
  if labels: notes.append("output: values rewritten as identifiers, originals under `labels`")
  if not columns and not problems: problems.append("no input field is left to type")
  schema = {"input": [field for field, _ in columns], "output": {"name": "output", "values": idents, **({"labels": labels} if labels else {})}}
  typed = [{**r, "typed_input": {field["name"]: column[i] for field, column in columns},
            "output": to_id[json.dumps(r["output"])] if idents else r["output"]} for i, r in enumerate(rows)]
  return schema, typed, notes, problems

def fail(error: str, **more: Any):
  stats.emit({"error": error, **more}, trim=tuple(more))
  sys.exit(1)

def main():
  parser = argparse.ArgumentParser(description="Turn one raw log into the layout the other scripts expect.")
  parser.add_argument("log", help="raw JSONL log")
  parser.add_argument("--schema", default=None, help="use this schema.json instead of inferring one (the cases must already fit it)")
  parser.add_argument("--extraction-step", action="store_true", help="the typed inputs were extracted from documents, free text or tool results")
  parser.add_argument("--drop", action="append", default=[], metavar="FIELD", help="leave this input field out (an identifier, free text)")
  parser.add_argument("--max-rows", type=int, default=None, metavar="N", help="keep only the latest N cases of a very large log")
  args = parser.parse_args()

  rows, other = [], {}  # other: what the raw lines carry next to the typed fields, with one example each
  try: lines = stats.read_jsonl(args.log)
  except ValueError: lines = [None]  # not JSON at all (a CSV): reported like any line that is not a case
  for n, raw in enumerate(lines):
    typed = raw.get("typed_input", raw.get("input")) if isinstance(raw, dict) else None
    if not isinstance(typed, dict) or "output" not in raw:
      fail(f"line {n + 1} needs `typed_input` (an object of fields) and `output`; JSON lines only: a CSV or JSON array has to be converted first")
    if not isinstance(raw.get("correct", True), bool): fail(f"line {n + 1}: `correct` must be true or false, not {json.dumps(raw['correct'])}")
    for key in set(raw) - {"case_id", "ts", "correct", "typed_input", "input", "output"} - stats.TOKEN_KEYS: other.setdefault(key, raw[key])
    tokens = stats.row_tokens(raw)  # NOTE: kept next to the typed input, never inside it: a token count must not become a rule input
    rows.append({"case_id": str(raw.get("case_id", f"c{n:05d}")), "ts": raw.get("ts", n), "typed_input": typed, "output": raw["output"],
                 **({} if tokens is None else {"tokens": tokens}), **({"correct": raw["correct"]} if "correct" in raw else {})})
  if not rows: fail("the log is empty")
  if 0 < sum("correct" in r for r in rows) < len(rows): fail("`correct` is on some lines but not on all: give every run its feedback, or none")
  rows.sort(key=lambda r: (str(r["ts"]) if not isinstance(r["ts"], (int, float)) else f"{r['ts']:020.6f}", r["case_id"]))
  notes: list[str] = []
  learning = None
  if len(rows) >= stats.MIN_TAIL and all("correct" in r for r in rows):  # NOTE: the loop's own feedback, read on the whole log
    correct = [r["correct"] for r in rows]
    steady = stats.steady_from(correct)  # from here on one and the same policy was scored, as far as the feedback can tell
    tail = correct[steady:]
    accuracy = sum(tail) / len(tail)
    before = correct[max(0, steady - len(tail)):steady]
    learning = {"converged": accuracy >= stats.PLATEAU, "runs": len(rows), "accuracy": round(accuracy, 3), "curve": stats.curve(rows)}
    slope = stats.drifting(tail)
    if slope:  # NOTE: still climbing or sinking across the steady stretch: the policy is still being changed
      learning["unsettled"] = {"recent": round(slope[1], 3), "before": round(slope[0], 3), "runs": len(tail), "drift": True}
      notes.append(f"the loop's feedback is still moving ({slope[0]:.0%} early in its last {len(tail)} runs, {slope[1]:.0%} at the end)")
    elif accuracy >= stats.PLATEAU and len(tail) >= stats.MIN_TAIL:
      if steady and sum(correct[:steady]) / steady >= stats.PLATEAU: steady = 0  # a dip in a loop that was right all along is not a learning phase
      if steady:
        learning |= {"from_run": steady, "accuracy_before": round(sum(correct[:steady]) / steady, 3)}
        learning["tokens"] = stats.token_stats(rows[:steady])
        notes.append(f"the loop held 95% accuracy after run {steady} of {len(rows)}: those {steady} runs are its learning phase, the rest is checked")
    elif accuracy < stats.PLATEAU and (len(tail) >= 2 * stats.MIN_TAIL or not steady):  # steady below 95%: examined as it is, it may be a guesser
      where = f"its steady stretch (from run {steady}) is examined" if steady else "the whole log is examined"
      notes.append(f"the loop's feedback holds at {accuracy:.0%}, below 95%: {where}")
    else:  # NOTE: the feedback changed within the last stretch of the log: there is no fixed policy to test yet
      was = sum(before) / len(before)
      learning["unsettled"] = {"recent": round(accuracy, 3), "runs": len(tail), "before": round(was, 3)}
      notes.append(f"the loop's feedback changed {len(tail)} runs before the end of the log ({accuracy:.0%} since, {was:.0%} before): INCONCLUSIVE")
    if "from_run" not in learning: learning["examined_from"] = steady  # a learning phase is set aside; anything else is examined from here
    rows = rows[steady:]
  if args.max_rows and len(rows) > args.max_rows:
    notes.append(f"kept the latest {args.max_rows} of {len(rows)} cases (--max-rows)")
    rows = rows[-args.max_rows:]
    if learning:
      learning["trimmed"] = True
      if learning["converged"] and "unsettled" not in learning: learning["accuracy"] = round(sum(r["correct"] for r in rows) / len(rows), 3)
  elif len(rows) > LARGE_LOG:
    notes.append(f"{len(rows)} cases: the fit and the Lean runs may take minutes or time out; --max-rows {LARGE_LOG} keeps the latest {LARGE_LOG}")

  if args.schema: schema, problems = stats.read_schema(args.schema), []
  else:
    schema, rows, said, problems = infer(rows, set(args.drop))
    notes += said
  try: types_src = lean.render_types(schema)
  except ValueError as exc: problems.append(f"{exc}: rename it in the schema (letters, digits and underscores, not a Lean keyword or type)")
  if problems: fail("the log cannot be typed yet", problems=problems)

  half = len(rows) // 2
  Path("logs").mkdir(exist_ok=True)
  for name, part in (("early", rows[:half]), ("heldout", rows[half:])):
    Path(f"logs/{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in part), encoding="utf-8")
  Path("schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
  Path("Types.lean").write_text(types_src, encoding="utf-8")
  meta: dict[str, Any] = {"n_total": len(rows), "n_early": half, "n_heldout": len(rows) - half}
  # NOTE: source material next to the typed fields and nothing says what it is: the audit then asks for the scope
  if args.extraction_step or not other: meta["pipeline"] = {"extraction_step": args.extraction_step}
  else: meta["source_fields"] = {"names": sorted(other), "sample": json.dumps(other[sorted(other)[0]])[:160]}
  if learning: meta["learning"] = learning
  if stats.token_stats(rows)["with_tokens"]: meta["tokens"] = stats.token_stats(rows)
  else: notes.append("no token count in the log lines (`tokens` or `usage` per case): token use and savings will be reported as unknown")
  Path("metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
  written = ["logs/early.jsonl", "logs/heldout.jsonl", "schema.json", "Types.lean", "metadata.json"]
  stats.emit({"written": written, **meta, "notes": notes, "schema": schema, "next": RUN_COMMAND}, trim=("notes",))

if __name__ == "__main__": main()
