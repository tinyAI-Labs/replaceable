# turn one raw log into the layout the other scripts expect
# usage: python3 scripts/prepare.py raw_log.jsonl [--schema schema.json] [--extraction-step] [--drop FIELD]... [--max-rows N]
# NOTE: input is JSONL, one logged case per line, with the typed input under `typed_input` (or `input`) and the loop's answer under
# `output`; `ts` (any sortable timestamp) and `case_id` are optional. It writes logs/early.jsonl (the earliest half, which the rule is
# fitted on), logs/heldout.jsonl (the latest half: only the replay reads it, and a host should move it out of the agent's reach),
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
  if present and all(isinstance(v, (bool, str)) for v in present) and len({type(v) for v in present}) == 1:
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
  for n, raw in enumerate(stats.read_jsonl(args.log)):
    typed = raw.get("typed_input", raw.get("input")) if isinstance(raw, dict) else None
    if not isinstance(typed, dict) or "output" not in raw: fail(f"line {n + 1} needs `typed_input` (an object of fields) and `output`")
    for key in set(raw) - {"case_id", "ts", "typed_input", "input", "output"} - stats.TOKEN_KEYS: other.setdefault(key, raw[key])
    tokens = stats.row_tokens(raw)  # NOTE: kept next to the typed input, never inside it: a token count must not become a rule input
    rows.append({"case_id": str(raw.get("case_id", f"c{n:05d}")), "ts": raw.get("ts", n), "typed_input": typed, "output": raw["output"],
                 **({} if tokens is None else {"tokens": tokens})})
  if not rows: fail("the log is empty")
  rows.sort(key=lambda r: (str(r["ts"]) if not isinstance(r["ts"], (int, float)) else f"{r['ts']:020.6f}", r["case_id"]))
  notes: list[str] = []
  if args.max_rows and len(rows) > args.max_rows:
    notes.append(f"kept the latest {args.max_rows} of {len(rows)} cases (--max-rows)")
    rows = rows[-args.max_rows:]
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
  if stats.token_stats(rows)["with_tokens"]: meta["tokens"] = stats.token_stats(rows)
  else: notes.append("no token count in the log lines (`tokens` or `usage` per case): token use and savings will be reported as unknown")
  Path("metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
  written = ["logs/early.jsonl", "logs/heldout.jsonl", "schema.json", "Types.lean", "metadata.json"]
  stats.emit({"written": written, **meta, "notes": notes, "schema": schema, "next": RUN_COMMAND}, trim=("notes",))

if __name__ == "__main__": main()
