# the boosted trees: learn the rules a policy is made of, learn nothing from noise, and stay deterministic
import random, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
from witness import boost

def schema(*fields, values=("a", "b")): return {"input": list(fields), "output": {"name": "output", "values": list(values)}}
NUMBERS = schema({"name": "x", "type": "nat"}, {"name": "y", "type": "nat"})

def cases(make, n, seed):
  rng = random.Random(seed)
  rows = []
  for i in range(n):
    x, out = make(rng)
    rows.append({"case_id": f"c{i}", "typed_input": x, "output": out})
  return rows

def accuracy(model, rows): return sum(p == r["output"] for p, r in zip(boost.predict(model, rows), rows)) / len(rows)

class TestLearns(unittest.TestCase):
  def test_a_threshold(self):
    def make(rng):
      x = {"x": rng.randrange(10_000), "y": rng.randrange(10_000)}
      return x, "a" if x["x"] > 6_000 else "b"
    model = boost.fit(cases(make, 400, 1), NUMBERS)
    self.assertGreater(accuracy(model, cases(make, 1000, 2)), 0.98)
  def test_an_interaction_of_two_thresholds(self):  # XOR: no single field decides, which a shallow rule per field cannot see
    def make(rng):
      x = {"x": rng.randrange(10_000), "y": rng.randrange(10_000)}
      return x, "a" if (x["x"] > 5_000) != (x["y"] > 5_000) else "b"
    model = boost.fit(cases(make, 800, 1), NUMBERS)
    self.assertGreater(accuracy(model, cases(make, 1000, 2)), 0.95)
  def test_categories_and_flags_and_three_outputs(self):
    fields = ({"name": "tier", "type": "enum", "values": ["free", "pro", "team"]}, {"name": "urgent", "type": "bool"},
              {"name": "amount", "type": "nat"})
    def make(rng):
      x = {"tier": rng.choice(["free", "pro", "team"]), "urgent": rng.random() < 0.3, "amount": rng.randrange(1_000)}
      return x, "c" if x["urgent"] else "b" if x["tier"] == "team" and x["amount"] > 300 else "a"
    model = boost.fit(cases(make, 600, 1), schema(*fields, values=("a", "b", "c")))
    self.assertEqual(model.classes, ("a", "b", "c"))
    self.assertGreater(accuracy(model, cases(make, 1000, 2)), 0.97)
  def test_a_rule_with_more_branches_than_a_person_reads(self):  # its own limit for each of 24 region and tier cells
    fields = ({"name": "region", "type": "enum", "values": list("abcdef")}, {"name": "tier", "type": "enum", "values": list("wxyz")},
              {"name": "amount", "type": "nat"})
    def make(rng):
      x = {"region": rng.choice("abcdef"), "tier": rng.choice("wxyz"), "amount": rng.randrange(10_001)}
      return x, "auto" if x["amount"] <= 1_000 + (ord(x["region"]) * 37 + ord(x["tier"]) * 91) % 17 * 500 else "manual"
    model = boost.fit(cases(make, 2000, 1), schema(*fields, values=("auto", "manual")))
    self.assertGreater(accuracy(model, cases(make, 1000, 2)), 0.95)

class TestDoesNotInvent(unittest.TestCase):
  def test_noise_is_not_learned(self):  # labels with nothing behind them: fresh cases are answered no better than by guessing
    def make(rng): return {"x": rng.randrange(10_000), "y": rng.randrange(10_000)}, rng.choice("ab")
    model = boost.fit(cases(make, 400, 1), NUMBERS)
    self.assertLess(accuracy(model, cases(make, 2000, 2)), 0.58)
  def test_one_output_is_a_constant(self):
    model = boost.fit(cases(lambda rng: ({"x": rng.randrange(100), "y": 1}, "a"), 200, 1), NUMBERS)
    self.assertEqual(boost.predict(model, cases(lambda rng: ({"x": 5, "y": 7}, "?"), 3, 2)), ["a"] * 3)

class TestRefuses(unittest.TestCase):  # a model is for choices; the skill reports free text and an empty log before it gets here
  def test_free_text_outputs(self):  # prepare.py writes no output values for free text
    rows = cases(lambda rng: ({"x": rng.randrange(100), "y": 1}, f"reply {rng.randrange(10_000)}"), 100, 1)
    with self.assertRaisesRegex(ValueError, "free text"): boost.fit(rows, schema({"name": "x", "type": "nat"}, values=()))
  def test_no_cases(self):
    with self.assertRaisesRegex(ValueError, "cases"): boost.fit([], NUMBERS)

class TestContract(unittest.TestCase):
  def make(self, rng):
    x = {"x": rng.randrange(1_000), "y": rng.randrange(1_000)}
    return x, "a" if x["x"] > 500 else "b"
  def test_the_same_cases_give_the_same_model(self):
    rows = cases(self.make, 300, 1)
    self.assertEqual(boost.fit(rows, NUMBERS), boost.fit(rows, NUMBERS))
  def test_inputs_are_not_changed_and_outputs_are_not_read_when_predicting(self):
    rows = cases(self.make, 300, 1)
    before = [dict(r) for r in rows]
    model = boost.fit(rows, NUMBERS)
    self.assertEqual(rows, before)
    # cases with no output, the second with a y far beyond anything in the log
    unseen = [{"case_id": "u", "typed_input": {"x": 900, "y": 3}}, {"case_id": "v", "typed_input": {"x": 10, "y": 99_999}}]
    self.assertEqual(boost.predict(model, unseen), ["a", "b"])
  def test_fields_that_are_not_decisions_are_ignored(self):
    fields = ({"name": "x", "type": "nat"}, {"name": "note", "type": "nat", "role": "context"})
    rows = [{**r, "typed_input": {**r["typed_input"], "note": random.Random(i).randrange(1_000)}} for i, r in enumerate(cases(self.make, 300, 1))]
    self.assertGreater(accuracy(boost.fit(rows, schema(*fields)), rows), 0.98)

if __name__ == "__main__": unittest.main()
