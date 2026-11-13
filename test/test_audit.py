# the checks before the exam: too few cases, free-text outputs, untypable values and the scope question
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import audit

FIELDS = [{"name": "amount", "type": "nat"}, {"name": "vip", "type": "bool"}, {"name": "tier", "type": "enum", "values": ["gold", "basic"]}]
SCHEMA = {"input": FIELDS, "output": {"name": "output", "values": ["approve", "deny"]}}

def triage(n, schema=SCHEMA, output="approve", amount=5, **more):
  cases = [{"case_id": f"c{i}", "typed_input": {"amount": amount, "vip": True, "tier": "gold"}, "output": output, **more} for i in range(n)]
  return audit.triage(cases, schema, {"n_total": 2 * n}, Path("no-such-file"), None, None)

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
