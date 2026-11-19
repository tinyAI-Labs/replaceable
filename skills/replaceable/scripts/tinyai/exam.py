# the model's exam: train on the early half, score the held-back half once, hand back numbers and nothing else
# NOTE: the model never leaves this function. What the skill keeps is how well it reproduced cases it had not seen, never the model itself.
from __future__ import annotations
from typing import Any, Sequence
import stats
from tinyai import boost

MAX_CASES = 5_000  # cases trained on, and scored: the work of a fit grows with the cases (stats.sample keeps a fixed, unbiased share)
CONFIDENCE = 0.99  # the model's bound is stricter than the rule's 95%: it is a second chance at the same exam, so it has to clear a higher bar

def exam(schema: stats.Schema, early: Sequence[stats.Row], heldout: Sequence[stats.Row]) -> dict[str, Any] | None:
  # {n, n_match, lower_bound, baseline, why} of a model on the held-back cases; None when there is nothing to learn from or to score
  if not early or not heldout or not schema["output"]["values"]: return None
  held = stats.sample(heldout, MAX_CASES)
  try: predicted = boost.predict(boost.fit(stats.sample(early, MAX_CASES), schema), held)
  except Exception: return None  # the model is optional: whatever goes wrong in it must not cost the one replay
  k = sum(p == r["output"] for p, r in zip(predicted, held))
  low = round(stats.clopper_pearson_lower(k, len(held), CONFIDENCE), 4)
  base = round(stats.baseline([r["output"] for r in held], schema["output"]["values"])[1], 4)
  return {"n": len(held), "n_match": k, "lower_bound": low, "baseline": base,
          "why": f"A tiny model trained on the early cases reproduced {k} of {len(held)} (99% lower bound {low}; always answering the most "
                 f"common output would match {base}). It is evidence that the typed fields decide the output; the model is discarded."}
