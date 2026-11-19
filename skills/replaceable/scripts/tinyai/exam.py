# the model's exam: train on the early half, score the held-back half once, hand back numbers and nothing else
# NOTE: the model never leaves this function. What the skill keeps is how well it reproduced cases it had not seen, never the model itself.
from __future__ import annotations
from typing import Any, Sequence
import stats
from tinyai import boost

MAX_CASES = 5_000  # cases trained on, and scored (stats.sample keeps a fixed, unbiased share)
MAX_WORK = 1_300_000  # cases x columns x outputs, about half a minute of fitting: a wide log trains on fewer cases, and on none when that is too few
CONFIDENCE = 0.99  # the model's bound is stricter than the rule's 95%: it is a second chance at the same exam, so it has to clear a higher bar

def exam(schema: stats.Schema, early: Sequence[stats.Row], heldout: Sequence[stats.Row]) -> dict[str, Any] | None:
  # {n, n_match, lower_bound, baseline, why} of a model on the held-back cases; None when there is nothing to learn from or to score
  if not early or not heldout or not schema["output"]["values"]: return None
  width = sum(len(f["values"]) if f["type"] == "enum" else 1 for f in schema["input"] if f["name"] in stats.decision_fields(schema))
  cap = min(MAX_CASES, MAX_WORK // (max(width, 1) * len(schema["output"]["values"])))
  if cap < stats.MIN_HELDOUT: return None
  held = stats.sample(heldout, cap)
  try: predicted = boost.predict(boost.fit(stats.sample(early, cap), schema), held)
  except Exception: return None  # the model is optional: whatever goes wrong in it must not cost the one replay
  k = sum(p == r["output"] for p, r in zip(predicted, held))
  low = round(stats.clopper_pearson_lower(k, len(held), CONFIDENCE), 4)
  base = round(stats.baseline([r["output"] for r in held], schema["output"]["values"])[1], 4)
  return {"n": len(held), "n_match": k, "lower_bound": low, "baseline": base,
          "why": f"A tiny model trained on the early cases reproduced {k} of {len(held)} (99% lower bound {low}; always answering the most "
                 f"common output would match {base}). It is evidence that the typed fields decide the output; the model is discarded."}
