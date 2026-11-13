# derived quantities: what a written policy computes from its fields before it compares
# NOTE: the solver compares single fields and ratios. When the rule it finds that way is weak, audit.py asks here for candidates, as
# Nat-valued Lean expressions of `x : Input`: the product of two numbers (a total), their difference in both directions (a gap; Nat
# subtraction stops at 0) and, for a number that is a YYYYMMDD date in every early case, month, day and weekday. Nothing here knows a
# loop: Lean evaluates the candidates, one fit over all of them shows which ones pruning keeps (`screen`), and audit.py lets those into
# the rule only if cross-validation on the early half improves. The quantities a reader offers with --derive take the same path.
from __future__ import annotations
from datetime import date
from itertools import combinations
from typing import Sequence
from fit import Fitter
from stats import Row, Schema, decision_fields

MAX_KEPT = 4  # candidates that may join the final fit
MIN_DATE_ROWS = 20  # early cases a field needs before "every value is a date" means something
DATE_RANGE = (1900, 2100)
KINDS = "products of two numbers, differences of two numbers, month, day and weekday of a date"

def _is_date(value: int) -> bool:
  year, month, day = value // 10_000, value // 100 % 100, value % 100
  if not DATE_RANGE[0] <= year <= DATE_RANGE[1]: return False
  try: date(year, month, day)
  except ValueError: return False
  return True

def date_fields(rows: Sequence[Row], schema: Schema) -> list[str]:
  # the number fields that hold a calendar date as YYYYMMDD in every early case
  numbers = [f["name"] for f in schema["input"] if f["type"] == "nat" and f["name"] in decision_fields(schema)]
  return [n for n in numbers if len(rows) >= MIN_DATE_ROWS and all(_is_date(r["typed_input"][n]) for r in rows)]

def date_parts(field: str) -> dict[str, str]:
  # month, day of the month and weekday (Zeller's congruence in Nat arithmetic) of a YYYYMMDD number: expression -> what it is
  month = f"x.{field} / 100 % 100"
  weekday = (f"let m := {month}; let y := x.{field} / 10000 - (14 - m) / 12; "
             f"(x.{field} % 100 + y + y / 4 - y / 100 + y / 400 + 31 * (m + 12 * ((14 - m) / 12) - 2) / 12 + 6) % 7")
  return {month: f"month of {field}", f"x.{field} % 100": f"day of the month of {field}", weekday: f"weekday of {field} (0 = Monday, 6 = Sunday)"}

def candidates(rows: Sequence[Row], schema: Schema) -> dict[str, str]:
  # candidate quantities for this schema: Lean expression of `x` -> what it is. Dates are split into parts, not multiplied.
  dates = date_fields(rows, schema)
  plain = {**schema, "input": [f for f in schema["input"] if f["name"] not in dates]}
  numbers = Fitter([r["typed_input"] for r in rows], [r["output"] for r in rows], plain).paired  # the most informative ones
  found = {expression: what for field in dates for expression, what in date_parts(field).items()}
  for a, b in combinations(numbers, 2):
    found[f"x.{a} * x.{b}"] = f"{a} times {b}"
    found.update({f"x.{a} - x.{b}": f"{a} minus {b} (0 when negative)", f"x.{b} - x.{a}": f"{b} minus {a} (0 when negative)"})
  return found

def widen(rows: Sequence[Row], schema: Schema, columns: dict[str, Sequence[int]]) -> tuple[list[Row], Schema]:
  # the rows and the schema with one extra number field per column (name -> its value for every row)
  wide_rows = [{**r, "typed_input": {**r["typed_input"], **{name: column[i] for name, column in columns.items()}}} for i, r in enumerate(rows)]
  return wide_rows, {**schema, "input": [*schema["input"], *({"name": name, "type": "nat", "derived": True} for name in columns)]}

def screen(rows: Sequence[Row], schema: Schema, columns: dict[str, Sequence[int]]) -> list[str]:
  # the candidate columns a fit over all of them uses after pruning: at most MAX_KEPT, the ones deciding most cases first
  wide_rows, wide_schema = widen(rows, schema, columns)
  weight: dict[str, int] = {}
  for node in Fitter([r["typed_input"] for r in wide_rows], [r["output"] for r in wide_rows], wide_schema).fit().inner():
    if node.test.a in columns: weight[node.test.a] = weight.get(node.test.a, 0) + len(node.rows)
  return sorted(weight, key=lambda name: -weight[name])[:MAX_KEPT]
