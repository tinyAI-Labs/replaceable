# the verdict table as code: every row, its boundaries, and the tokens block that must never move a verdict
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import stats, verdict

def report(n=200, k=200, base=0.5, constant=False, low=None, **more):
  low = round(stats.clopper_pearson_lower(k, n), 4) if low is None else low
  return {"n_heldout": n, "n_match": k, "agreement": round(k / n, 4), "lower_bound": low, "baseline": base, "all_outputs_constant": constant, **more}

def fallback(inside=40, outside=160, misses_outside=0, bound=0.97):
  return {"fallback": {"inside": inside, "outside": outside, "misses_outside": misses_outside, "outside_lower_bound": bound}}

class TestVerdictTable(unittest.TestCase):
  def name(self, replay_): return verdict.verdict_from_replay(replay_)[0]
  def test_failed_replay(self): self.assertEqual(self.name({"failure": "replay_timeout"}), "INCONCLUSIVE")
  def test_too_few_heldout(self):
    self.assertEqual(self.name(report(n=59, k=59, low=0.99)), "INCONCLUSIVE")
    self.assertEqual(self.name(report(n=60, k=60)), "DETERMINISTIC")  # 60 of 60 gives L = 0.9513
  def test_constant(self):
    self.assertEqual(self.name(report(constant=True, base=1.0)), "CONSTANT")
    self.assertEqual(self.name(report(constant=True, base=1.0, low=0.9499)), "INCONCLUSIVE")
  def test_deterministic_bound(self):
    self.assertEqual(self.name(report(low=0.95)), "DETERMINISTIC")
    self.assertEqual(self.name(report(low=0.9499)), "INCONCLUSIVE")
  def test_deterministic_needs_to_beat_the_baseline(self):
    self.assertEqual(self.name(report(low=0.98, base=0.96)), "DETERMINISTIC")
    self.assertEqual(self.name(report(low=0.9799, base=0.96)), "INCONCLUSIVE")
    self.assertIn("tell a rule from a constant", verdict.verdict_from_replay(report(low=0.9799, base=0.96))[1])
  def test_core_fallback(self):
    self.assertEqual(self.name(report(low=0.85, **fallback())), "DETERMINISTIC_CORE_FALLBACK")
    self.assertEqual(self.name(report(low=0.80, **fallback(bound=0.95))), "DETERMINISTIC_CORE_FALLBACK")
    for weak in (fallback(outside=59), fallback(inside=0), fallback(bound=0.9499), {}):
      self.assertEqual(self.name(report(low=0.85, **weak)), "INCONCLUSIVE")
    self.assertEqual(self.name(report(low=0.7999, **fallback())), "INCONCLUSIVE")
  def test_weak_rule_is_never_probabilistic(self):
    name, why = verdict.verdict_from_replay(report(k=100))
    self.assertEqual(name, "INCONCLUSIVE")
    self.assertIn("does not show the loop is probabilistic", why)
  def test_interrupted_replay_is_inconclusive(self):
    self.assertEqual(verdict.verdict_from_replay({"rule_sha256": "abc", "failure": "replay_interrupted"})[0], "INCONCLUSIVE")

class TestVerdictBuild(unittest.TestCase):
  state = {"triage": {"scope": "full"}, "fit": {"rule_sha256": stats.sha256("rule")}}
  def receipt(self, **more): return {"rule_sha256": stats.sha256("rule"), **report(**more)}
  def test_exits_before_the_rule(self):
    for name, stage in (("OUT_OF_SCOPE", "triage"), ("INCONCLUSIVE", "triage"), ("INCONCLUSIVE", "types"), ("PROBABILISTIC", "conflicts")):
      state = {"proposed": {"verdict": name, "exit_stage": stage, "why": "w"}, "conflicts": {"example_pairs": [["a", "b"], ["c", "d"]]}}
      built = verdict.build(state, None, None, None, False)
      self.assertEqual((built["verdict"], built["exit_stage"]), (name, stage))
      self.assertEqual("conflict_pairs" in built, name == "PROBABILISTIC")
  def test_no_replay(self):
    self.assertEqual(verdict.build(self.state, None, "rule", None, False)["needs"], "replay")
    self.assertEqual(verdict.build(self.state, None, "rule", None, True)["exit_stage"], "checker")
  def test_rule_changed_after_replay(self):
    self.assertEqual(verdict.build(self.state, self.receipt(), "edited", None, False)["verdict"], "INCONCLUSIVE")
    self.assertEqual(verdict.build(self.state, self.receipt(), "rule", None, False)["verdict"], "DETERMINISTIC")
  def test_scope(self):
    asked = {**self.state, "triage": {"scope": "full", "scope_question": "q"}}
    self.assertEqual(verdict.build(asked, self.receipt(), "rule", None, False)["needs"], "scope")
    built = verdict.build(asked, self.receipt(), "rule", "decision_step_only", False)
    self.assertEqual(built["scope"], "decision_step_only")
    self.assertIn("extraction step", built["rationale"])
    self.assertNotIn("scope", verdict.build(asked, self.receipt(k=100), "rule", None, False))  # no deterministic claim, no scope needed

class TestTokenBlock(unittest.TestCase):
  def block(self, name, early=None, receipt=None, **more): return verdict.token_block({"tokens": early}, receipt, {"verdict": name, **more})
  def test_block_arithmetic(self):
    early, held = stats.token_stats([{"tokens": 1000}] * 100), stats.token_stats([{"tokens": 1200}] * 100)
    receipt = {"n_heldout": 100, "tokens": held, **fallback(inside=25, outside=75)}
    full = self.block("DETERMINISTIC", early, receipt)
    keys = ("per_run", "logged", "cases_with_tokens", "rule_share", "saved_per_run", "saved_logged")
    self.assertEqual([full[k] for k in keys], [1100.0, 220_000, 200, 1.0, 1100.0, 220_000])
    core = self.block("DETERMINISTIC_CORE_FALLBACK", early, receipt)
    self.assertEqual([core[k] for k in ("rule_share", "saved_per_run", "saved_logged")], [0.75, 825.0, 165_000])  # 75 of 100 held-back cases
    self.assertIn("saved over the log = 0.75 x 220000 = 165000", core["arithmetic"])
    self.assertEqual(self.block("CONSTANT", early, receipt)["rule_share"], 1.0)
    for name in ("INCONCLUSIVE", "PROBABILISTIC", "OUT_OF_SCOPE"):
      self.assertEqual([self.block(name, early)[k] for k in ("saved_logged", "logged")], [0, 100_000])
    self.assertIn("upper bound", self.block("DETERMINISTIC", early, receipt, scope="decision_step_only")["note"])
    self.assertNotIn("note", full)
  def test_unknown_without_counts(self):
    for state_tokens, receipt in ((None, None), (stats.token_stats([{}]), {"n_heldout": 100})):
      self.assertEqual(self.block("DETERMINISTIC", state_tokens, receipt)["known"], False)
  def test_tokens_do_not_reach_the_verdict(self):
    state, receipt = {"triage": {"scope": "full"}, "fit": {}}, {"rule_sha256": stats.sha256("rule"), **report()}
    counted = ({**state, "tokens": stats.token_stats([{"tokens": 5}])}, {**receipt, "tokens": stats.token_stats([{"tokens": 9}])})
    self.assertEqual(verdict.build(*counted, "rule", None, False), verdict.build(state, receipt, "rule", None, False))

if __name__ == "__main__": unittest.main()
