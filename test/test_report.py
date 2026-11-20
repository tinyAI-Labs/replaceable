import contextlib, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import report, stats

TOKENS = {"known": True, "per_run": 1200.0, "logged": 1_000_000, "cases_with_tokens": 800, "rule_share": 1.0, "per_month": 3_000_000,
          "learning": {"from_run": 200, "accuracy_before": 0.7, "tokens": {"with_tokens": 200, "mean": 3000.0}}}
RESULT = {"verdict": "DETERMINISTIC", "scope": "full", "rationale": "The rule matched <all> of them.", "tokens": TOKENS,
          "n_heldout": 400, "lower_bound": 0.99, "agreement": 1.0, "baseline": 0.5,
          "learning": {"converged": True, "runs": 1000, "accuracy": 0.99, "from_run": 200, "accuracy_before": 0.7,
                       "curve": [[0.6, 3000.0], [0.8, 2000.0], [0.99, 1200.0], [1.0, 1200.0]]}}

class TestCurve(unittest.TestCase):
  def test_blocks_of_the_log_with_accuracy_and_mean_tokens(self):
    rows = [{"correct": i >= 50, "tokens": 100 if i >= 50 else 300} for i in range(100)]
    self.assertEqual(stats.curve(rows, blocks=4), [[0.0, 300.0], [0.0, 300.0], [1.0, 100.0], [1.0, 100.0]])
  def test_a_block_without_token_counts_has_none(self):
    self.assertEqual(stats.curve([{"correct": True}] * 10, blocks=2), [[1.0, None], [1.0, None]])

class TestFacts(unittest.TestCase):
  def test_the_numbers_a_reader_needs(self):
    said = {label: (value, note) for label, value, note in report.facts(RESULT, 3.0)}
    self.assertEqual(said["Learning phase"], ("200 runs", "70% right, set aside"))
    self.assertEqual(said["Learning phase cost"], ("2.5x per run", "3,000 tokens per run"))
    self.assertEqual(said["Tokens per month"], ("3,000,000", "$9.00 at $3 per million"))
    self.assertEqual(said["A rule would take over"], ("100% of runs", "3,000,000 tokens a month"))
    self.assertEqual(said["Held-back replay"][0], "100.0% matched")
  def test_a_log_without_counts_or_feedback_has_no_such_rows(self):
    bare = {"verdict": "INCONCLUSIVE", "rationale": "r", "tokens": {"known": False}}
    self.assertEqual(report.facts(bare), [])

class TestRendering(unittest.TestCase):
  def test_spark_scales_between_its_bounds_and_marks_gaps(self):
    self.assertEqual(report.spark([0.5, 0.75, 1.0, None], 0.5, 1.0), "▁▅█·")
  def test_terminal_keeps_the_answer_and_tokens_lines_and_names_the_files(self):
    lines = report.terminal(RESULT).splitlines()
    self.assertEqual(lines[0], "replaceable · DETERMINISTIC (full)")
    self.assertTrue(lines[1].startswith("Answer: Yes, deterministic.") and lines[2].startswith("Tokens: this loop used 1,200 per run"))
    self.assertIn("report.html", lines[3])
  def test_markdown_has_the_table_the_curves_and_the_next_step(self):
    text = report.markdown(RESULT)
    self.assertIn("| Tokens per run | **1,200** |", text)
    self.assertIn("right per block  ", text)
    self.assertIn("60% → 100%", text)
    self.assertIn("The loop held 95% from run 200.", text)
    self.assertIn("## Next", text)
  def test_html_is_one_escaped_self_contained_page(self):
    page = report.page(RESULT)
    self.assertIn("&lt;all&gt;", page)
    self.assertNotIn("<all>", page)
    self.assertIn("<svg", page)
    self.assertNotIn("http://", page.replace("https://tinyailabs.com/", ""))  # no remote resource, only the closing question's link
    self.assertNotIn("src=", page)
  def test_no_ask_for_a_loop_that_cannot_be_replaced(self):
    guesser = RESULT | {"verdict": "PROBABILISTIC"}
    self.assertNotIn("tinyailabs", report.markdown(guesser))
    self.assertNotIn("tinyailabs", report.page(guesser))
  def test_write_makes_both_files(self):
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
      report.write(RESULT)
      self.assertTrue(Path("report.md").is_file() and Path("report.html").is_file())

if __name__ == "__main__": unittest.main()
