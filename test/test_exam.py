# the model's exam: what the held-back half says about a model trained on the early half, and when it must say nothing
import random, sys, unittest
from unittest import mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import stats
from witness import exam

SCHEMA = {"input": [{"name": "x", "type": "nat"}, {"name": "y", "type": "nat"}], "output": {"name": "output", "values": ["a", "b"]}}

def cases(make, n, seed, start=0):
  rng = random.Random(seed)
  rows = []
  for i in range(n):
    x = {"x": rng.randrange(10_000), "y": rng.randrange(10_000)}
    rows.append({"case_id": f"c{start + i}", "typed_input": x, "output": make(x, rng)})
  return rows

class TestExam(unittest.TestCase):
  def test_a_function_of_the_inputs_passes_with_its_numbers(self):
    def threshold(x, rng): return "a" if x["x"] > 6_000 else "b"
    result = exam.exam(SCHEMA, cases(threshold, 600, 1), cases(threshold, 600, 2, 600))
    self.assertEqual(set(result), {"n", "n_match", "lower_bound", "baseline", "why"})
    self.assertEqual(result["n"], 600)
    self.assertGreaterEqual(result["lower_bound"], stats.BOUND_DETERMINISTIC)
    self.assertEqual(result["lower_bound"], round(stats.clopper_pearson_lower(result["n_match"], 600, exam.CONFIDENCE), 4))
  def test_noise_does_not_pass(self):
    result = exam.exam(SCHEMA, cases(lambda x, rng: rng.choice("ab"), 600, 1), cases(lambda x, rng: rng.choice("ab"), 600, 2, 600))
    self.assertLess(result["lower_bound"], 0.7)
  def test_free_text_and_an_empty_half_say_nothing(self):
    rows = cases(lambda x, rng: "a", 10, 1)
    self.assertIsNone(exam.exam({**SCHEMA, "output": {"name": "output", "values": []}}, rows, rows))
    self.assertIsNone(exam.exam(SCHEMA, rows, []))
  def test_a_model_that_cannot_be_trained_says_nothing(self):  # the optional witness must never cost the one replay
    self.assertIsNone(exam.exam(SCHEMA, [{"case_id": "c0", "typed_input": {"x": object()}, "output": "a"}], cases(lambda x, rng: "a", 5, 1)))
  def test_a_huge_log_is_sampled(self):
    rows = cases(lambda x, rng: "a" if x["x"] > 5_000 else "b", exam.MAX_CASES + 500, 1)
    self.assertEqual(exam.exam(SCHEMA, rows[:300], rows)["n"], exam.MAX_CASES)

class TestWork(unittest.TestCase):  # a wide log trains on fewer cases (the work grows with cases x columns x outputs) instead of running for minutes
  def wide(self, fields, outputs):
    fields = [{"name": f"f{i}", "type": "nat"} for i in range(fields)]
    schema = {"input": fields, "output": {"name": "output", "values": [f"o{i}" for i in range(outputs)]}}
    rng = random.Random(1)
    rows = [{"case_id": f"c{i}", "typed_input": {f["name"]: rng.randrange(1_000) for f in fields}, "output": f"o{i % outputs}"} for i in range(600)]
    with mock.patch.object(exam, "MAX_WORK", 6_000): return exam.exam(schema, rows, rows)
  def test_a_wide_log_uses_fewer_cases(self): self.assertEqual(self.wide(10, 3)["n"], 6_000 // 30)
  def test_a_log_too_wide_to_try_says_nothing(self): self.assertIsNone(self.wide(100, 10))  # 6000 // 1000 = 6 cases, fewer than a verdict needs

if __name__ == "__main__": unittest.main()
