# gradient-boosted trees: a small model that learns a loop's answers from the early cases, to find out whether the typed fields decide them
# NOTE: a model is trained in memory for one exam and thrown away. Nothing here writes a file or keeps a model, so the skill reports what the
# model showed about the loop and hands over nothing that could stand in for it. There is no randomness: the same cases give the same model
# (on one machine: a floating-point tie may break differently on another). Standard library only. The work grows with cases x fields x
# ROUNDS x outputs, so callers cap the cases.
from __future__ import annotations
from bisect import bisect_left
from math import exp
from typing import NamedTuple, Sequence
from stats import Row, Schema, decision_fields

ROUNDS, DEPTH, MAX_BINS = 100, 4, 64  # trees per output, tree depth, cut points per number
MIN_LEAF, MIN_GAIN = 5, 1e-9  # cases a leaf needs; the least a split has to gain
RATE, LAMBDA = 0.3, 1.0  # the step each tree takes; shrinks leaf values towards 0, so a leaf with few cases says little

type Tree = float | tuple[int, int, Tree, Tree]  # a leaf value, or (column, cut bin, tree for bins up to the cut, tree for the rest)

class Model(NamedTuple):
  schema: Schema
  classes: tuple[str, ...]  # the outputs seen, sorted
  edges: tuple[tuple[int, ...], ...]  # the cut points of each column
  trees: tuple[tuple[Tree, ...], ...]  # per round, one tree per output

class Binned(NamedTuple):
  columns: list[list[int]]  # per column, the bin of every case
  sizes: list[int]  # per column, its number of bins

def encode(schema: Schema, rows: Sequence[Row]) -> list[list[int]]:
  # a number or a flag is one column, a category one 0/1 column per value; only the decision fields, in schema order
  fields = {f["name"]: f for f in schema["input"]}
  found: list[list[int]] = []
  for name in decision_fields(schema):
    values = [r["typed_input"][name] for r in rows]
    if fields[name]["type"] == "enum": found += [[int(v == option) for v in values] for option in fields[name]["values"]]
    else: found.append([int(v) for v in values])
  return found

def cuts(column: Sequence[int]) -> tuple[int, ...]:
  # every distinct value but the largest, or MAX_BINS - 1 evenly spaced ones when there are more
  distinct = sorted(set(column))
  if len(distinct) <= MAX_BINS: return tuple(distinct[:-1])
  return tuple(sorted({distinct[len(distinct) * i // MAX_BINS] for i in range(1, MAX_BINS)}))

def to_bins(column: Sequence[int], edges: Sequence[int]) -> list[int]:
  # bin b holds the values up to edges[b]; the last bin holds what lies beyond every edge
  return [bisect_left(edges, v) for v in column]

def best_split(data: Binned, grad: list[float], hess: list[float], rows: list[int]) -> tuple[float, int, int] | None:
  # (gain, column, last bin of the low side) of the best split of these cases, None if no split leaves MIN_LEAF cases on each side
  total_g, total_h = sum(grad[i] for i in rows), sum(hess[i] for i in rows)
  base, best = total_g * total_g / (total_h + LAMBDA), None
  for f, column in enumerate(data.columns):
    g, h, n = [0.0] * data.sizes[f], [0.0] * data.sizes[f], [0] * data.sizes[f]
    for i in rows:
      g[column[i]] += grad[i]
      h[column[i]] += hess[i]
      n[column[i]] += 1
    low_g = low_h = 0.0
    low_n = 0
    for cut in range(data.sizes[f] - 1):
      low_g, low_h, low_n = low_g + g[cut], low_h + h[cut], low_n + n[cut]
      if low_n < MIN_LEAF or len(rows) - low_n < MIN_LEAF: continue
      gain = low_g * low_g / (low_h + LAMBDA) + (total_g - low_g) ** 2 / (total_h - low_h + LAMBDA) - base
      if best is None or gain > best[0]: best = (gain, f, cut)
  return best

def grow(data: Binned, grad: list[float], hess: list[float], rows: list[int], depth: int) -> Tree:
  # one tree fitted to the gradients; a leaf holds the step (a Newton step, shrunk by LAMBDA and RATE) its cases take
  split = best_split(data, grad, hess, rows) if depth else None
  if split is None or split[0] <= MIN_GAIN: return -RATE * sum(grad[i] for i in rows) / (sum(hess[i] for i in rows) + LAMBDA)
  _, f, cut = split
  column = data.columns[f]
  low, high = [i for i in rows if column[i] <= cut], [i for i in rows if column[i] > cut]
  return (f, cut, grow(data, grad, hess, low, depth - 1), grow(data, grad, hess, high, depth - 1))

def walk(tree: Tree, binned: Sequence[Sequence[int]], i: int) -> float:
  # the leaf value of case i: follow its bins down the tree
  while isinstance(tree, tuple):
    f, cut, low, high = tree
    tree = low if binned[f][i] <= cut else high
  return tree

def softmax(scores: Sequence[float]) -> list[float]:
  top = max(scores)
  weights = [exp(s - top) for s in scores]
  return [w / sum(weights) for w in weights]

def fit(rows: Sequence[Row], schema: Schema) -> Model:
  # learn the output of each case from its decision fields; one output seen means a constant model
  if not rows: raise ValueError("boosting needs cases to learn from")
  if not schema["output"]["values"]: raise ValueError("the output is free text, not a choice among values: there is nothing to boost")
  classes = tuple(sorted({r["output"] for r in rows}))
  found = encode(schema, rows)
  edges = tuple(cuts(c) for c in found)
  if len(classes) == 1: return Model(schema, classes, edges, ())
  data = Binned([to_bins(c, e) for c, e in zip(found, edges)], [len(e) + 1 for e in edges])
  answer = [classes.index(r["output"]) for r in rows]
  scores, trees = [[0.0] * len(classes) for _ in rows], []
  for _ in range(ROUNDS):
    probs = [softmax(s) for s in scores]
    forest = []
    for k in range(len(classes)):
      grad = [p[k] - (a == k) for p, a in zip(probs, answer)]
      hess = [max(p[k] * (1 - p[k]), 1e-6) for p in probs]
      forest.append(grow(data, grad, hess, list(range(len(rows))), DEPTH))
      for i, s in enumerate(scores): s[k] += walk(forest[k], data.columns, i)
    trees.append(tuple(forest))
  return Model(schema, classes, edges, tuple(trees))

def predict(model: Model, rows: Sequence[Row]) -> list[str]:
  # the output of each case, from its decision fields alone (a case needs no `output`)
  if len(model.classes) == 1: return [model.classes[0]] * len(rows)
  data = [to_bins(c, e) for c, e in zip(encode(model.schema, rows), model.edges)]
  scores = [[sum(walk(forest[k], data, i) for forest in model.trees) for k in range(len(model.classes))] for i in range(len(rows))]
  return [model.classes[max(range(len(s)), key=s.__getitem__)] for s in scores]
