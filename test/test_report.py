import contextlib, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import report, stats

TOKENS = {"known": True, "per_run": 1200.0, "logged": 1_000_000, "cases_with_tokens": 800, "rule_share": 1.0, "per_month": 3_000_000,
          "learning": {"from_run": 200, "accuracy_before": 0.7, "tokens": {"with_tokens": 200, "mean": 3000.0}}}
RESULT = {"verdict": "DETERMINISTIC", "scope": "full", "loop": {"reads": ["amount", "tier"], "answers": ["approve", "deny"]},
          "rules": [{"when": "tier is gold", "then": "approve", "share": 0.6}], "rationale": "The rule matched <all> of them.", "tokens": TOKENS,
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

class TestCosts(unittest.TestCase):
  def said(self, result, usd=None): return {label: (value, note) for label, value, note in report.cost_rows(result, usd)}
  def test_what_the_loop_costs_per_run_at_scale_and_while_it_learned(self):
    said = self.said(RESULT, 3.0)
    self.assertEqual(said["Per run"], ("1,200 tokens", "once it works"))
    self.assertEqual(said["Per month"], ("3,000,000 tokens", "about $9.00 at $3 per million"))
    self.assertEqual(said["While it was learning"], ("2.5x as much per run", "3,000 tokens, the first 200 runs, 70% right"))
  def test_without_a_pace_the_log_total_and_the_price_are_shown(self):
    paceless = RESULT | {"tokens": {k: val for k, val in TOKENS.items() if k != "per_month"} | {"saved_logged": 1_000_000}}
    self.assertEqual(self.said(paceless, 2.0)["In the log"], ("1,000,000 tokens", "about $2.00 at $2 per million"))
    self.assertEqual(report.saving(paceless), "A fixed rule runs this for 0 tokens: 1,000,000 tokens over the log saved.")
  def test_nothing_is_said_about_cost_without_a_token_count(self):
    bare = {"verdict": "INCONCLUSIVE", "rationale": "r", "tokens": {"known": False}}
    self.assertEqual((report.cost_rows(bare), report.saving(bare), report.evidence_rows(bare)), ([], None, []))
  def test_a_saving_only_where_a_rule_takes_over(self):
    self.assertEqual(report.saving(RESULT), "A fixed rule runs this for 0 tokens: about 3,000,000 tokens a month saved.")
    self.assertIsNone(report.saving(RESULT | {"tokens": TOKENS | {"rule_share": 0.0}}))

class TestTheLoop(unittest.TestCase):
  LOOP = {"reads": ["amount", "tier"], "answers": ["approve", "deny"]}
  def test_the_loop_in_one_sentence(self):
    self.assertEqual(report.loop_text({"loop": self.LOOP}), "Reads amount, tier and decides one of 2 outcomes (approve, deny).")
    many = {"reads": [f"f{i}" for i in range(8)], "answers": []}
    self.assertEqual(report.loop_text({"loop": many}), "Reads f0, f1, f2, f3, f4 and 3 more and writes free text.")
    self.assertIsNone(report.loop_text({}))
  def test_the_main_rules_in_words(self):
    rules = [{"when": "tier is not gold", "then": "deny", "share": 0.456}, {"when": "everywhere", "then": "approve", "share": 0.3}]
    self.assertEqual(report.rule_lines({"rules": rules}), ["46% of runs: tier is not gold → deny", "30% of runs: always approve"])

class TestEvidence(unittest.TestCase):
  def said(self, result): return {label: (value, note) for label, value, note in report.evidence_rows(result)}
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
  def test_terminal_names_the_loop_the_answer_the_cost_and_the_question(self):
    lines = report.terminal(RESULT).splitlines()
    self.assertEqual(lines[0], "replaceable · DETERMINISTIC (full)")
    self.assertEqual(lines[1], "Loop: Reads amount, tier and decides one of 2 outcomes (approve, deny).")
    self.assertTrue(lines[2].startswith("Answer: Yes, deterministic.") and lines[3].startswith("Tokens: 1,200 per run"))
    self.assertIn("tinyailabs.com", lines[4])
    self.assertIn("report.html", lines[5])
  def test_markdown_leads_with_the_verdict_in_words_then_the_loop_the_cost_and_the_saving(self):
    text = report.markdown(RESULT)
    heads = ("# Predictable", "## The loop", "60% of runs: tier is gold → approve", "## What it costs", "## What a fixed rule would cost",
             "## The loop over time", "## The evidence")
    order = [text.index(h) for h in heads]
    self.assertEqual(order, sorted(order))
    self.assertIn("| Per run | **1,200 tokens** |", text)
    self.assertIn("60% → 100%", text)
    self.assertIn("The loop held 95% from run 200.", text)
  def test_html_is_one_escaped_self_contained_page(self):
    page = report.page(RESULT)
    self.assertIn("&lt;all&gt;", page)
    self.assertNotIn("<all>", page)
    self.assertIn("<svg", page)
    self.assertNotIn("http://", page.replace("https://tinyailabs.com/", ""))  # no remote resource, only the closing question's link
    self.assertNotIn("src=", page)
    self.assertIn("<details", page)  # the evidence is folded away: the page opens on the verdict, the loop and the cost
  def test_no_ask_for_a_loop_that_cannot_be_replaced(self):
    guesser = RESULT | {"verdict": "PROBABILISTIC"}
    self.assertNotIn("tinyailabs", report.markdown(guesser))
    self.assertNotIn("tinyailabs", report.page(guesser))
  def test_write_makes_both_files(self):
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
      report.write(RESULT)
      self.assertTrue(Path("report.md").is_file() and Path("report.html").is_file())

if __name__ == "__main__": unittest.main()
