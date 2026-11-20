# the checks before the exam: too few cases, free-text outputs, untypable values and the scope question
import random, sys, unittest
from unittest import mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import audit

FIELDS = [{"name": "amount", "type": "nat"}, {"name": "vip", "type": "bool"}, {"name": "tier", "type": "enum", "values": ["gold", "basic"]}]
SCHEMA = {"input": FIELDS, "output": {"name": "output", "values": ["approve", "deny"]}}

def triage(n, schema=SCHEMA, output="approve", amount=5, **more):
  cases = [{"case_id": f"c{i}", "typed_input": {"amount": amount, "vip": True, "tier": "gold"}, "output": output, **more} for i in range(n)]
  return audit.triage(cases, schema, {"n_total": 2 * n}, Path("no-such-file"), None, None)

class TestDerivedQuantitiesAreTried(unittest.TestCase):  # a rule that is strong but still misses cases may be improved by a derived quantity
  def cases(self, policy):
    rng = random.Random(3)
    xs = [{"a": rng.randrange(1, 100), "b": rng.randrange(1, 100)} for _ in range(1200)]
    return [{"case_id": f"c{i}", "typed_input": x, "output": policy(x)} for i, x in enumerate(xs)]
  def searched(self, policy):
    schema = {"input": [{"name": "a", "type": "nat"}, {"name": "b", "type": "nat"}], "output": {"name": "output", "values": ["approve", "deny"]}}
    with mock.patch.object(audit, "candidates", return_value={}) as candidates: audit.fit_best(self.cases(policy), schema, "", [])
    return candidates.called
  def test_a_strong_rule_with_early_misses_still_searches(self):
    self.assertTrue(self.searched(lambda x: "approve" if x["a"] * x["b"] <= 1500 else "deny"))
  def test_a_whole_rule_does_not(self):
    self.assertFalse(self.searched(lambda x: "approve" if x["a"] <= 50 else "deny"))

class TestNextStep(unittest.TestCase):
  def test_a_loop_whose_feedback_is_still_moving_goes_straight_to_the_verdict(self):  # no rule check, scope question or replay can change it
    step = audit.next_step({"learning": {"unsettled": {"recent": 0.9, "before": 0.7}}})
    self.assertEqual(step["action"], "verdict")

class TestTriage(unittest.TestCase):
  def test_case_count(self):
    self.assertNotIn("exit", triage(75))
    self.assertEqual((triage(74)["exit"], triage(74)["exit_stage"]), ("INCONCLUSIVE", "triage"))  # 148 cases in total: under 150
  def test_free_text_output(self):
    self.assertEqual(triage(75, {**SCHEMA, "output": {"name": "output", "values": []}})["exit"], "OUT_OF_SCOPE")
  def test_values_outside_the_types(self):
    self.assertEqual(triage(75, amount=-1)["exit_stage"], "types")
    self.assertEqual(triage(75, output="maybe")["exit_stage"], "types")
  def test_token_keys_are_not_source_material(self):
    self.assertNotIn("scope_question", triage(75, tokens=9, usage={}))
    self.assertIn("scope_question", triage(75, tokens=9, usage={}, email="text"))  # a real source field does ask

if __name__ == "__main__": unittest.main()
