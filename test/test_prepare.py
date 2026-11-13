# how a raw log is typed: numbers, dates, enums, names, and what must stop the run
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import prepare

class TestPrepareTyping(unittest.TestCase):
  def typed(self, values, raw="f"): return prepare.type_field(raw, values, prepare.field_namer())
  def test_numbers(self):
    (field, column), = self.typed([3, 12, 0])[0]
    self.assertEqual((field["type"], column), ("nat", [3, 12, 0]))
    columns, notes, problem = self.typed([12.5, -3, None])
    self.assertEqual([(f["name"], f["type"], c) for f, c in columns],
                     [("f", "nat", [125, 30, 0]), ("f_negative", "bool", [False, True, False]), ("f_missing", "bool", [False, False, True])])
    self.assertEqual((columns[0][0]["scale"], problem, len(notes)), (10, None, 3))
    self.assertIn("more than 6 places", self.typed([0.1234567])[2])
  def test_dates_bools_enums(self):
    (field, column), = self.typed(["2024-03-21", "2024-12-01T10:00:00"])[0]
    self.assertEqual((field["type"], field["unit"], column), ("nat", "calendar date as YYYYMMDD", [20240321, 20241201]))
    self.assertEqual(self.typed([True, False])[0][0][0]["type"], "bool")
    (field, column), = self.typed(["in-review", "2nd", None, "ok"])[0]
    self.assertEqual((field["values"], column), (["in_review", "missing", "ok", "v_2nd"], ["in_review", "v_2nd", "missing", "ok"]))
    self.assertEqual(field["labels"], {"v_2nd": "2nd", "in_review": "in-review", "missing": None})
  def test_untypable(self):
    self.assertIn("free text or an identifier", self.typed([f"id{i}" for i in range(21)])[2])
    self.assertIn("--drop", self.typed([{"nested": 1}])[2])
    self.assertIn("--drop", self.typed([1, "one"])[2])
  def test_names(self):
    namer = prepare.field_namer()
    names = [namer(raw) for raw in ("customer id", "def", "input", "Input", "derived1")]
    self.assertEqual(names, ["customer_id", "v_def", "input_field", "Input_field_2", "derived1_field"])
  def test_infer_keeps_tokens_out_of_the_typed_input(self):
    logged = [{"case_id": c, "typed_input": {"n": n}, "output": o, "tokens": t} for c, n, o, t in (("a", 1, True, 10), ("b", 2, False, 30))]
    schema, typed, _, problems = prepare.infer(logged, set())
    self.assertEqual(([f["name"] for f in schema["input"]], schema["output"]["values"], problems), (["n"], ["v_false", "v_true"], []))
    self.assertEqual(typed[1], {"case_id": "b", "typed_input": {"n": 2}, "output": "v_false", "tokens": 30})
    free_text = [{"typed_input": {"n": i}, "output": f"text {i}"} for i in range(21)]
    self.assertEqual(prepare.infer(free_text, set())[0]["output"]["values"], [])

if __name__ == "__main__": unittest.main()
