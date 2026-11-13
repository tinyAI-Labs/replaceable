# the solver: fit a small decision tree to the EARLY half and render it as Lean, with the exact residual
# NOTE: the tests are what any typed schema offers: an enum equals a value / is one of a group, a bool is true, a number is at most a
# threshold, the ratio of two numbers is at most a threshold (`a * D <= N * b`). Other quantities reach it as extra number fields marked
# `derived` (derive.py), compared with thresholds only. The tree is capped at MAX_LEAVES and pruned by description length, so it cannot
# memorise cases. Leaves that still mix outputs are the residual; if they cover a minority of the cases they become the proposed
# `fallback` region, widened by a safety margin. It never reads the held-out half: every number here describes the early half only.
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from math import inf, log2
from typing import Any, NamedTuple, Sequence
from lean import MAX_RULE_BYTES, pascal
from stats import BOUND_DETERMINISTIC, Row, Schema, decision_fields, output_order
from thresholds import pick_threshold, strict

MAX_LEAVES = 16  # size cap of the rule, so that a person can read and sign it off
GROW_LEAVES = 4 * MAX_LEAVES  # the search grows this far before pruning back
RISING = 0.02  # gain in cross-validated agreement from doubling the cases that counts as "still rising"
MAX_PAIR_FIELDS = 6  # numbers considered for ratio tests (the most informative ones)
FALLBACK_MAX_COVER = 0.5  # residual leaves covering more than this share are "spread", not a region
FALLBACK_MARGIN = 2  # neighbouring early cases a fallback bound is widened by
CV_FOLDS = 5
NUMERIC = ("le", "ratio")
EPS = 1e-9  # a gain must beat the best so far by this much, so ties go to the test tried first

class Test(NamedTuple):
  # one yes/no question about an input; numeric tests read `num(x) * den <= thr * den(x)`, in integers
  kind: str  # "switch" (one branch per enum value), "is" (enum equals value), "in" (enum is one of a group), "true" (bool), "le" or "ratio"
  a: str
  b: str | None = None  # second field of a ratio
  value: str | None = None  # enum value of an "is" test
  values: tuple[str, ...] = ()  # enum values of a "switch" test, one branch each, or the group of an "in" test
  thr: Fraction = Fraction(0)

  @property
  def arity(self) -> int: return len(self.values) if self.kind == "switch" else 2
  # which child a case goes to; yes/no tests: 0 when the test holds, 1 when it does not
  def branch(self, x: dict[str, Any]) -> int: return self.values.index(x[self.a]) if self.kind == "switch" else (0 if self.holds(x) else 1)

  def holds(self, x: dict[str, Any]) -> bool:
    if self.kind == "is": return x[self.a] == self.value
    if self.kind == "in": return x[self.a] in self.values
    if self.kind == "true": return bool(x[self.a])
    num, den = _parts(self, x)
    return num * self.thr.denominator <= self.thr.numerator * den

  def lean(self, branch: int = 0) -> str:
    # the condition for taking `branch`, as a Lean proposition (ASCII operators, so any terminal can print it)
    if self.kind == "switch": return f"x.{self.a} = {pascal(self.a)}.{self.values[branch]}"
    return self._lean_yes() if branch == 0 else f"Not ({self._lean_yes()})"

  def _lean_yes(self) -> str:
    n, d = self.thr.numerator, self.thr.denominator
    if self.kind == "is": return f"x.{self.a} = {pascal(self.a)}.{self.value}"
    if self.kind == "in": return f"([{', '.join('.' + v for v in self.values)}] : List {pascal(self.a)}).contains x.{self.a} = true"
    if self.kind == "true": return f"x.{self.a} = true"
    if self.kind == "ratio": return f"x.{self.a} * {d} <= {n} * x.{self.b}"
    return f"x.{self.a} < {n + 1}" if strict(n) else f"x.{self.a} <= {n}"

  def text(self, branch: int = 0) -> str:
    yes = branch == 0
    if self.kind == "switch": return f"{self.a} = {self.values[branch]}"
    if self.kind == "is": return f"{self.a} {'=' if yes else '!='} {self.value}"
    if self.kind == "in": return f"{self.a} {'in' if yes else 'not in'} {{{', '.join(self.values)}}}"
    if self.kind == "true": return f"{self.a} = {str(yes).lower()}"
    left = self.a if self.kind == "le" else f"{self.a} / {self.b}"
    if self.kind != "ratio" and strict(self.thr.numerator): return f"{left} {'<' if yes else '>='} {self.thr + 1}"
    return f"{left} {'<=' if yes else '>'} {self.thr}"

Conds = Sequence[tuple[Test, int]]  # (test, branch) steps: the path from the root to a node, or one alternative of a fallback region

# numerator and denominator of a numeric test's value
def _parts(test: Test, x: dict[str, Any]) -> tuple[int, int]: return (x[test.a], x[test.b]) if test.kind == "ratio" else (x[test.a], 1)

def _value(test: Test, x: dict[str, Any]) -> Fraction | float:
  # the value a numeric test compares; a ratio over zero is 0 (0/0) or infinite, matching `holds`
  num, den = _parts(test, x)
  return Fraction(num, den) if den else (Fraction(0) if num == 0 else inf)

@dataclass
class Node:
  rows: list[int]  # indices of the early cases that reach this node
  counts: Counter[str]
  label: str
  test: Test | None = None
  kids: tuple[Node, ...] = ()  # one child per branch of `test`

  @property
  def misses(self) -> int: return len(self.rows) - self.counts[self.label]

  def leaves(self, path: Conds = ()) -> list[tuple[Node, Conds]]:
    # every leaf with the (test, branch) steps that lead to it
    if self.test is None: return [(self, path)]
    return [found for b, kid in enumerate(self.kids) for found in kid.leaves((*path, (self.test, b)))]

  def inner(self) -> list[Node]: return [] if self.test is None else [self, *(n for kid in self.kids for n in kid.inner())]

  def predict(self, x: dict[str, Any]) -> str:
    node = self
    while node.test is not None: node = node.kids[node.test.branch(x)]
    return node.label

def _entropy_bits(counts: Counter[str]) -> float:
  # bits needed to write down the outputs of a group given only their frequencies
  n = sum(counts.values())
  return -sum(c * log2(c / n) for c in counts.values() if c)

class Fitter:
  def __init__(self, inputs: Sequence[dict[str, Any]], outputs: Sequence[str], schema: Schema):
    self.x, self.y = inputs, outputs
    self.order = output_order(schema, [{"output": o} for o in outputs])
    fields = {f["name"]: f for f in schema["input"] if f["name"] in decision_fields(schema)}
    self.discrete = [Test("is", n, value=v) for n, f in fields.items() if f["type"] == "enum" for v in f["values"]]
    self.switches = [Test("switch", n, values=tuple(f["values"])) for n, f in fields.items() if f["type"] == "enum" and len(f["values"]) > 2]
    self.grouped = [n for n, f in fields.items() if f["type"] == "enum" and len(f["values"]) > 3]  # enums with room for a group of values
    self.discrete += [Test("true", n) for n, f in fields.items() if f["type"] == "bool"]
    numbers = [n for n, f in fields.items() if f["type"] == "nat"]
    self._values: dict[Test, list[Fraction | float]] = {}  # each numeric axis evaluated once per case
    self._ranks: dict[Test, list[int]] = {}  # ... and the position of each case's value among the distinct values of the axis
    self._code = [self.order.index(o) if o in self.order else len(self.order) for o in outputs]  # outputs as small integers
    self._xlogx = [0.0, *(k * log2(k) for k in range(1, len(inputs) + 1))]
    self.axes = [Test("le", n) for n in numbers]
    paired = [n for n in numbers if not fields[n].get("derived")]  # a derived quantity is only compared with a threshold
    if len(paired) > MAX_PAIR_FIELDS:  # keep the pair tests affordable: pair only the most informative numbers
      gains = {n: self._best_cut(list(range(len(inputs))), Test("le", n))[0] for n in paired}
      paired = sorted(paired, key=lambda n: -gains[n])[:MAX_PAIR_FIELDS]
    self.paired = paired
    for i, a in enumerate(paired): self.axes += [Test("ratio", a, b) for b in paired[i + 1:]]
    self.test_bits = log2(max(len(self.discrete) + len(self.grouped) + len(self.axes), 2))  # bits to name the test of a split

  def _node(self, rows: list[int], default: str | None = None) -> Node:
    # a leaf answering the most frequent output of its cases (`default` when it has none)
    counts = Counter(self.y[i] for i in rows)
    return Node(rows, counts, max(self.order, key=lambda v: (counts[v], -self.order.index(v))) if rows else default)

  def _gain(self, parent: Counter, left: Counter) -> float: return _entropy_bits(parent) - _entropy_bits(left) - _entropy_bits(parent - left)

  def _split(self, node: Node, test: Test):
    parts: list[list[int]] = [[] for _ in range(test.arity)]
    for i in node.rows: parts[test.branch(self.x[i])].append(i)
    node.test, node.kids = test, tuple(self._node(part, node.label) for part in parts)

  def _best_cut(self, rows: list[int], axis: Test) -> tuple[float, Test | None]:
    # the best threshold on one numeric axis: (gain in bits, test)
    if axis not in self._values:
      values = self._values[axis] = [_value(axis, x) for x in self.x]
      position = {v: k for k, v in enumerate(sorted(set(values)))}
      self._ranks[axis] = [position[v] for v in values]
    values, ranks, code, xlogx = self._values[axis], self._ranks[axis], self._code, self._xlogx
    ranked = sorted(rows, key=ranks.__getitem__)
    # NOTE: entropy in bits of a group with counts c is xlogx[n] - sum(xlogx[c]); both sums are kept up to date case by case
    left, right = [0] * (len(self.order) + 1), [0] * (len(self.order) + 1)
    for i in rows: right[code[i]] += 1
    n, sum_left, sum_right = len(rows), 0.0, sum(xlogx[c] for c in right)
    whole, best, cut = xlogx[n] - sum_right, 0.0, None
    for pos, i in enumerate(ranked[:-1]):
      c = code[i]
      sum_left += xlogx[left[c] + 1] - xlogx[left[c]]
      sum_right += xlogx[right[c] - 1] - xlogx[right[c]]
      left[c], right[c] = left[c] + 1, right[c] - 1
      if ranks[i] < ranks[ranked[pos + 1]]:
        gain = whole - (xlogx[pos + 1] - sum_left) - (xlogx[n - pos - 1] - sum_right)
        if gain > best + EPS: best, cut = gain, pos
    if cut is None: return 0.0, None
    return best, axis._replace(thr=pick_threshold(axis.kind, values[ranked[cut]], values[ranked[cut + 1]]))

  def _best_group(self, rows: list[int], field: str) -> tuple[float, Test | None]:
    # NOTE: the best split of an enum field's values into a group and the rest. For each output, the values are ordered by the share of
    # their cases with that output and every cut of that order is tried, as for a threshold. Values no case here has stay outside.
    by_value: dict[str, Counter[str]] = {}
    for i in rows: by_value.setdefault(self.x[i][field], Counter())[self.y[i]] += 1
    parent = sum(by_value.values(), Counter())
    best, group = 0.0, None
    for output in parent:
      ranked = sorted(by_value, key=lambda v: (-by_value[v][output] / sum(by_value[v].values()), v))
      left: Counter[str] = Counter()
      for k, value in enumerate(ranked[:-1], 1):
        left += by_value[value]
        gain = self._gain(parent, left)
        # a group of one, or all but one, is an "is" test
        if 1 < k < len(ranked) - 1 and gain > best + EPS: best, group = gain, tuple(sorted(ranked[:k]))
    return best, None if group is None else Test("in", field, values=group)

  def _best_split(self, node: Node, enums: str) -> tuple[float, Test | None]:
    # NOTE: the most informative test at a node; simpler kinds win ties (they are tried first). `enums` says what an enum field may be
    # asked besides "is it this value": nothing more ("is"), one branch per value ("switch") or membership in a group ("group").
    best, chosen = 0.0, None
    if len(node.counts) < 2: return best, chosen
    for test in self.switches if enums == "switch" else []:
      parts = Counter((test.branch(self.x[i]), self.y[i]) for i in node.rows)
      gain = _entropy_bits(node.counts) - sum(_entropy_bits(Counter({o: c for (b, o), c in parts.items() if b == k})) for k in range(test.arity))
      if gain > best + EPS: best, chosen = gain, test
    for test in self.discrete:
      left = Counter(self.y[i] for i in node.rows if test.holds(self.x[i]))
      if 0 < sum(left.values()) < len(node.rows) and (gain := self._gain(node.counts, left)) > best + EPS: best, chosen = gain, test
    for gain, test in [self._best_group(node.rows, field) for field in (self.grouped if enums == "group" else [])]:
      if gain > best + EPS: best, chosen = gain, test
    for axis in self.axes:
      gain, test = self._best_cut(node.rows, axis)
      if gain > best + EPS: best, chosen = gain, test
    return best, chosen

  def _prune(self, node: Node) -> float:
    # bottom-up: keep a split only if it shortens the description (outputs given the tree, plus the tree); returns the bits
    as_leaf = _entropy_bits(node.counts)
    if node.test is None: return as_leaf
    cut_bits = log2(len(node.rows)) if node.test.kind in NUMERIC else 0.0  # where the threshold sits among the cases
    if node.test.kind == "in": cut_bits = float(len({self.x[i][node.test.a] for i in node.rows}))  # which of the values seen here are in the group
    as_tree = sum(self._prune(kid) for kid in node.kids) + self.test_bits + cut_bits
    if as_tree < as_leaf: return as_tree
    node.test, node.kids = None, ()
    return as_leaf

  def fit(self, rows: list[int] | None = None) -> Node:
    # NOTE: the shortest description of up to three searches, which differ in what they may ask an enum field. "Is it this value" pools
    # cases across the other values (sharper thresholds); one branch per value gives each value its own thresholds (per-category
    # policies); membership in a group serves enums whose values fall into classes. The simpler search wins a tie.
    rows = list(range(len(self.x))) if rows is None else rows
    modes = ("is", *(("switch",) if self.switches else ()), *(("group",) if self.grouped else ()))
    return min((self._search(rows, enums) for enums in modes), key=lambda c: c[0])[1]

  def _search(self, rows: list[int], enums: str) -> tuple[float, Node]:
    # grow best-first, prune by description length, cap at MAX_LEAVES; returns (bits, tree)
    root = self._node(rows)
    frontier = [(root, *self._best_split(root, enums))]
    while len(root.leaves()) < GROW_LEAVES:
      node, _, test = max(frontier, key=lambda entry: entry[1])
      if test is None: break
      frontier = [entry for entry in frontier if entry[0] is not node]
      self._split(node, test)
      frontier += [(kid, *self._best_split(kid, enums)) for kid in node.kids]
    self._prune(root)
    while len(root.leaves()) > MAX_LEAVES:  # still too large: undo the least informative of the last splits
      twigs = [n for n in root.inner() if all(kid.test is None for kid in n.kids)]
      weakest = min(twigs, key=lambda n: _entropy_bits(n.counts) - sum(_entropy_bits(kid.counts) for kid in n.kids))
      weakest.test, weakest.kids = None, ()
    return self._prune(root), root

  def _widen(self, path: Conds) -> list[tuple[Test, int]]:
    # NOTE: a residual leaf's conditions with each numeric bound moved outward past FALLBACK_MARGIN neighbouring cases: the edge of a
    # noisy region is only located to within the spacing of the data, so the fallback is declared wider
    widened = []
    for k, (test, branch) in enumerate(path):
      if test.kind not in NUMERIC:
        widened.append((test, branch))
        continue
      others = [step for j, step in enumerate(path) if j != k]
      beyond = sorted({_value(test, x) for x in self.x if all(t.branch(x) == b for t, b in others) and test.branch(x) != branch}, reverse=branch == 1)
      if len(beyond) <= FALLBACK_MARGIN: continue  # few cases lie beyond the bound: drop it
      inner, outer = beyond[FALLBACK_MARGIN - 1], beyond[FALLBACK_MARGIN]
      low, high = (inner, outer) if branch == 0 else (outer, inner)
      widened.append((test._replace(thr=pick_threshold(test.kind, low, high)), branch))
    return widened

  def fallback(self, root: Node) -> list[list[tuple[Test, int]]]:
    # the proposed fallback region as a list of condition lists (any one list holding puts a case inside), or []
    residual = [(leaf, path) for leaf, path in root.leaves() if leaf.misses]
    covered = sum(len(leaf.rows) for leaf, _ in residual)
    if not residual or covered > FALLBACK_MAX_COVER * len(root.rows) or any(not path for _, path in residual): return []
    return [self._widen(path) for _, path in residual]

def in_region(region: Sequence[Conds], x: dict) -> bool: return any(all(test.branch(x) == branch for test, branch in conds) for conds in region)
def conds_text(conds: Conds) -> str: return " and ".join(test.text(branch) for test, branch in conds) or "everywhere"
def root_misses(root: Node) -> int: return sum(leaf.misses for leaf, _ in root.leaves())

# *** rendering (NOTE: the rule text is hashed into receipts and verdicts, so its bytes must not change) ***

def _lean_expr(node: Node, indent: int) -> str:
  labels = {leaf.label for leaf, _ in node.leaves()}
  if len(labels) == 1: return f"Output.{labels.pop()}"  # a subtree that answers the same everywhere (its leaves differ only in purity)
  pad, kids = " " * indent, [_lean_expr(kid, indent + 2) for kid in node.kids]
  if node.test.kind != "switch": return f"if {node.test.lean()} then\n{pad}  {kids[0]}\n{pad}else\n{pad}  {kids[1]}"
  arms: dict[str, list[str]] = {}  # enum values with the same answer share an arm
  for value, body in zip(node.test.values, kids): arms.setdefault(body, []).append(f"{pascal(node.test.a)}.{value}")
  return f"(match x.{node.test.a} with" + "".join(f"\n{pad}| {' | '.join(values)} =>\n{pad}  {body}" for body, values in arms.items()) + ")"

def render_lean(root: Node, region: Sequence[Conds]) -> str:
  # Rule.lean: `f` as nested if-then-else and, when a region is proposed, `fallback`
  arg = "x" if len({leaf.label for leaf, _ in root.leaves()}) > 1 else "_"
  source = f"-- Fitted to the early half of the log by scripts/fit.py.\ndef f ({arg} : Input) : Output :=\n  {_lean_expr(root, 2)}\n"
  if not region: return source
  inside = " \\/\n     ".join("(" + (" /\\ ".join(test.lean(branch) for test, branch in conds) or "True") + ")" for conds in region)
  return source + ("\n-- The region handed back to the LLM: the rule's early misses all sit inside it.\n"
                   f"def fallback (x : Input) : Bool :=\n  if {inside}\n  then true else false\n")

# *** the whole fit ***

def fit_report(rows: Sequence[Row], schema: Schema) -> tuple[dict[str, Any], str]:
  # fit the early rows; returns (report, Rule.lean source)
  inputs, outputs = [r["typed_input"] for r in rows], [r["output"] for r in rows]
  fitter = Fitter(inputs, outputs, schema)
  root = fitter.fit()
  region = fitter.fallback(root)
  source = render_lean(root, region)
  if len(source.encode()) > MAX_RULE_BYTES and region: region, source = [], render_lean(root, [])  # keep the rule under the checker's size cap
  n, missed = len(rows), root_misses(root)
  # NOTE: cross-validation inside the early half (refit on four fifths, predict the rest) is the honest preview of the replay
  cv_hits = cv_outside = cv_outside_hits = 0
  for k in range(CV_FOLDS) if n >= 2 * CV_FOLDS else []:
    tree = Fitter(inputs, outputs, schema).fit([i for i in range(n) if i % CV_FOLDS != k])
    for i in range(k, n, CV_FOLDS):
      hit = tree.predict(inputs[i]) == outputs[i]
      cv_hits += hit
      if not in_region(region, inputs[i]): cv_outside, cv_outside_hits = cv_outside + 1, cv_outside_hits + hit
  leaves = root.leaves()
  residual = [{"where": conds_text(path), "n": len(leaf.rows), "misses": leaf.misses, "outputs": dict(leaf.counts)}
              for leaf, path in sorted(leaves, key=lambda lp: -lp[0].misses) if leaf.misses]
  used = sorted({name for _, path in leaves for test, _ in path for name in (test.a, test.b) if name})
  inside = sum(in_region(region, x) for x in inputs)
  report: dict[str, Any] = {
    "slice": "early", "n": n, "leaves": len(leaves), "fields_used": used, "fields_unused": [f for f in decision_fields(schema) if f not in used],
    "early_agreement": round(1 - missed / n, 4), "early_misses": missed, "cv_agreement": round(cv_hits / n, 4) if n >= 2 * CV_FOLDS else None,
    "residual": residual, "residual_reading": "none" if not missed else ("region" if region else "spread")}
  if region:
    report["fallback"] = {"region": " OR ".join(f"({conds_text(conds)})" for conds in region), "early_inside": inside, "early_outside": n - inside,
                          "cv_agreement_outside": round(cv_outside_hits / cv_outside, 4) if cv_outside else None}
  return report, source

def forecast(report: dict[str, Any]) -> tuple[float, str]:
  # NOTE: the early half's honest preview of the exam, (agreement, what it measures): the cross-validated agreement, outside the fallback
  # region when one is proposed (the in-sample figure there is zero misses by construction and says nothing)
  region = report.get("fallback", {})
  if region.get("cv_agreement_outside") is not None: return region["cv_agreement_outside"], "cross-validated, outside the fallback region"
  if report["cv_agreement"] is not None: return report["cv_agreement"], "cross-validated"
  return report["early_agreement"], "of the early cases"

def learning(rows: Sequence[Row], schema: Schema) -> dict[str, Any] | None:
  # NOTE: does the rule still improve with more cases? The cross-validation of `fit_report` again, on half of each training part. A weak
  # rule whose agreement rises when the cases double is short of evidence, not of a rule to find. A diagnostic, never a verdict.
  inputs, outputs, n = [r["typed_input"] for r in rows], [r["output"] for r in rows], len(rows)
  if n < 4 * CV_FOLDS: return None
  hits = 0
  for k in range(CV_FOLDS):
    tree = Fitter(inputs, outputs, schema).fit([i for i in range(n) if i % CV_FOLDS != k and (i // CV_FOLDS) % 2 == 0])
    hits += sum(tree.predict(inputs[i]) == outputs[i] for i in range(k, n, CV_FOLDS))
  return {"cv_agreement_on_half_the_cases": round(hits / n, 4)}

def rank(report: dict[str, Any]) -> tuple[int, float]:
  # how good a fitted rule looks on the early half, to choose between two fits: first its class (2: strong and whole, 1: strong outside
  # a proposed fallback region, 0: weak), then its cross-validated agreement overall
  strong = forecast(report)[0] >= BOUND_DETERMINISTIC
  return (2 - ("fallback" in report) if strong else 0), report["cv_agreement"] if report["cv_agreement"] is not None else report["early_agreement"]
