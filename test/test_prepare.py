# how a raw log is typed: numbers, dates, enums, names, and what must stop the run
import json, subprocess, sys, tempfile, unittest
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
  def test_numbers_mixed_with_text_become_a_category(self):  # UCI Car: doors is 2, 3, 4 or "5more"
    columns, _, problem = self.typed([2, 3, "5more", 4, 2, "5more"])
    self.assertIsNone(problem)
    (field, column), = columns
    self.assertEqual(field["type"], "enum")
    self.assertEqual([len(set(column)), column[0] == column[4], column[2] == column[5]], [4, True, True])
    _, _, problem = self.typed([*range(30), "many"])  # still too many values to be a category
    self.assertIn("distinct text values", problem)
  def test_untypable(self):
    self.assertIn("free text or an identifier", self.typed([f"id{i}" for i in range(21)])[2])
    self.assertIn("--drop", self.typed([{"nested": 1}])[2])
    self.assertIn("--drop", self.typed([1, {"nested": 1}])[2])  # a number with nested data; a number with text is a category
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


class TestPrepareLearning(unittest.TestCase):  # `correct` on every run sets the learning phase aside
  def test_the_runs_before_convergence_are_not_the_log(self):
    rows = [{"case_id": f"r{i:03d}", "ts": i, "tokens": 800 if i < 300 else 100, "typed_input": {"n": i % 7}, "output": "a" if i % 7 < 3 else "b",
             "correct": i % 5 >= 2 if i < 300 else True} for i in range(700)]
    with tempfile.TemporaryDirectory() as tmp:
      (Path(tmp) / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
      subprocess.run([sys.executable, str(Path(prepare.__file__)), "raw.jsonl"], cwd=tmp, capture_output=True, text=True, check=True)
      meta, schema = (json.loads((Path(tmp) / name).read_text()) for name in ("metadata.json", "schema.json"))
      kept = [json.loads(line) for name in ("early", "heldout") for line in (Path(tmp) / "logs" / f"{name}.jsonl").read_text().splitlines()]
    self.assertEqual((meta["n_total"], meta["n_early"], meta["n_heldout"]), (400, 200, 200))
    self.assertEqual((meta["learning"]["converged"], meta["learning"]["from_run"], meta["learning"]["runs"]), (True, 300, 700))
    self.assertEqual((meta["learning"]["accuracy_before"], meta["learning"]["tokens"]["mean"]), (0.6, 800))  # the runs set aside keep their numbers
    self.assertEqual(meta["tokens"]["mean"], 100)
    self.assertEqual({r["case_id"] for r in kept}, {f"r{i:03d}" for i in range(300, 700)})
    self.assertEqual([f["name"] for f in schema["input"]], ["n"])  # feedback is never an input of the rule

  def prepare(self, rows, *args):
    with tempfile.TemporaryDirectory() as tmp:
      (Path(tmp) / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
      done = subprocess.run([sys.executable, str(Path(prepare.__file__)), "raw.jsonl", *args], cwd=tmp, capture_output=True, text=True, check=False)
      meta = json.loads((Path(tmp) / "metadata.json").read_text()) if done.returncode == 0 else None
    return done.returncode, done.stdout + done.stderr, meta
  def run_of(self, n, correct):
    return [{"case_id": f"r{i:04d}", "ts": i, "typed_input": {"n": i % 7}, "output": "a" if i % 7 < 3 else "b", "correct": correct(i)}
            for i in range(n)]
  def test_feedback_must_be_true_or_false_on_every_run(self):
    rows = self.run_of(400, lambda i: True)
    code, said, _ = self.prepare([*rows[:5], {**rows[5], "correct": "true"}, *rows[6:]])
    self.assertEqual(code, 1)
    self.assertIn("line 6: `correct` must be true or false", said)
    code, said, _ = self.prepare([*rows[:5], {k: v for k, v in rows[5].items() if k != "correct"}, *rows[6:]])
    self.assertEqual(code, 1)
    self.assertIn("on some lines but not on all", said)
  def test_a_loop_that_never_holds_95_percent_is_examined_on_its_latest_half(self):
    code, said, meta = self.prepare(self.run_of(1000, lambda i: i % 5 != 0))
    self.assertEqual(code, 0)
    self.assertEqual((meta["learning"]["converged"], meta["learning"]["examined_from"], meta["learning"]["accuracy"]), (False, 500, 0.8))
    self.assertEqual(meta["n_total"], 500)
  def test_a_loop_whose_feedback_is_still_moving_is_marked_unsettled(self):
    rising = lambda i: i % 5 >= 2 if i < 700 else i % 10 != 0  # noqa: E731  60%, then 90%: still rising
    _, _, meta = self.prepare(self.run_of(1000, rising))
    self.assertEqual((meta["learning"]["converged"], meta["learning"]["unsettled"]), (False, {"recent": 0.9, "before": 0.66}))
    _, _, meta = self.prepare(self.run_of(100, lambda i: False))  # too short to have a learning phase at all
    self.assertNotIn("learning", meta)
  def test_flat_below_95_in_a_short_log_is_examined_whole(self):
    _, _, meta = self.prepare(self.run_of(260, lambda i: i % 5 != 0))
    self.assertEqual((meta["learning"]["examined_from"], meta["n_total"]), (0, 260))  # halving 260 runs would leave under 150
  def test_a_log_that_was_good_from_the_start_has_no_learning_phase(self):
    _, _, meta = self.prepare(self.run_of(600, lambda i: i % 25 != 0))  # 96% throughout
    self.assertNotIn("from_run", meta["learning"])
  def test_the_learning_phase_is_found_before_max_rows_trims_the_log(self):
    code, _, meta = self.prepare(self.run_of(1000, lambda i: i >= 300 or i % 5 >= 2), "--max-rows", "400")
    self.assertEqual((code, meta["learning"]["from_run"], meta["learning"]["runs"], meta["n_total"]), (0, 300, 1000, 400))
    self.assertTrue(meta["learning"]["trimmed"])

class TestPrepareInput(unittest.TestCase):  # a log that is not JSON lines must be told so, not crash
  def run_on(self, text):
    with tempfile.TemporaryDirectory() as tmp:
      (Path(tmp) / "raw.txt").write_text(text, encoding="utf-8")
      done = subprocess.run([sys.executable, str(Path(prepare.__file__)), "raw.txt"], cwd=tmp, capture_output=True, text=True, check=False)
    return done.returncode, done.stdout + done.stderr
  def test_csv_and_json_array_are_told_to_convert(self):
    for text in ("amount,tier,output\n10,gold,approve\n", '[{"typed_input": {"amount": 10}, "output": "approve"}]'):
      code, said = self.run_on(text)
      self.assertEqual(code, 1)
      self.assertNotIn("Traceback", said)
      self.assertIn("converted first", said)

if __name__ == "__main__": unittest.main()
