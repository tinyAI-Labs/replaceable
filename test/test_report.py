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
  def test_the_last_block_takes_the_remainder(self):
    rows = [{"correct": i < 99} for i in range(103)]
    blocks = stats.curve(rows, blocks=4)  # blocks of 25, the last of 28
    self.assertEqual((len(blocks), blocks[-1][0]), (4, round(24 / 28, 3)))
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

class TestFactsEdges(unittest.TestCase):
  def said(self, result, usd=None): return {label: (value, note) for label, value, note in report.facts(result, usd)}
  def test_a_loop_right_from_the_start_is_not_called_below_95(self):
    right = {"verdict": "OUT_OF_SCOPE", "rationale": "r", "tokens": {"known": False}, "learning": {"converged": True, "runs": 500, "accuracy": 1.0}}
    self.assertEqual(self.said(right)["Feedback"], ("100% right", "steady, at or above 95%"))
  def test_figures_below_the_bar_are_rounded_down(self):
    self.assertEqual((report.floor1(0.9496), report.floor1(0.95), report.floor1(1.0)), ("94.9%", "95.0%", "100.0%"))
    self.assertEqual(self.said(RESULT | {"lower_bound": 0.9496})["Held-back replay"][1], "400 cases, 95% lower bound 94.9%")
  def test_the_learned_verdict_shows_the_models_numbers_not_the_rules(self):
    learned = RESULT | {"verdict": "DETERMINISTIC_LEARNED", "model": {"n": 200, "n_match": 198, "lower_bound": 0.955, "baseline": 0.4}}
    said = self.said(learned)
    self.assertEqual(said["Learned model, held-back"], ("99.0% matched", "200 cases, 99% lower bound 95.5%"))
    self.assertNotIn("Held-back replay", said)
  def test_a_trimmed_log_counts_the_runs_examined(self):
    trimmed = RESULT | {"learning": RESULT["learning"] | {"trimmed": True}}
    self.assertEqual(self.said(trimmed)["Converged loop"], ("800 runs", "99% right, examined, latest only"))
  def test_a_decision_step_saving_is_an_upper_bound(self):
    bound = RESULT | {"tokens": TOKENS | {"note": "upper bound"}}
    self.assertEqual(self.said(bound)["A rule would take over"][1], "3,000,000 tokens a month, an upper bound")
  def test_without_a_pace_the_log_total_and_the_price_are_shown(self):
    paceless = RESULT | {"tokens": {k: val for k, val in TOKENS.items() if k != "per_month"} | {"saved_logged": 1_000_000}}
    self.assertEqual(self.said(paceless, 2.0)["Tokens in the log"], ("1,000,000", "$2.00 at $2 per million"))
    self.assertEqual(self.said(paceless)["A rule would take over"][1], "1,000,000 tokens in the log")
  def test_the_next_step_follows_where_an_inconclusive_verdict_came_from(self):
    self.assertIn("steady for 300", report.next_step({"verdict": "INCONCLUSIVE", "exit_stage": "feedback"}))
    self.assertIn("rationale", report.next_step({"verdict": "INCONCLUSIVE", "exit_stage": "replay"}))
  def test_a_block_below_the_axis_stays_inside_its_panel(self):
    low = RESULT | {"learning": RESULT["learning"] | {"curve": [[0.3, 3000.0], [1.0, 1200.0]]}}
    ys = [float(pair.split(",")[1]) for pair in report.page(low).split('points="')[1].split('"')[0].split()]
    self.assertTrue(all(0 <= y <= 150 for y in ys), ys)

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
