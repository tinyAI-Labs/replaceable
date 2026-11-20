# the verdict table as code: every row, its boundaries, and the tokens block that must never move a verdict
import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import report as page, stats, verdict

def report(n=200, k=200, base=0.5, constant=False, low=None, **more):
  low = round(stats.clopper_pearson_lower(k, n), 4) if low is None else low
  return {"n_heldout": n, "n_match": k, "agreement": round(k / n, 4), "lower_bound": low, "baseline": base, "all_outputs_constant": constant, **more}

def model(n=200, k=200, base=0.5, low=None):  # what the learned model scored on the held-back cases, at the 99% bound
  low = round(stats.clopper_pearson_lower(k, n, 0.99), 4) if low is None else low
  return {"model": {"n": n, "n_match": k, "why": "A learned model reproduced them.", "baseline": base, "lower_bound": low}}

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
  def test_a_rule_below_the_bar_is_inconclusive_however_close_it_is(self):
    for low in (0.9499, 0.85, 0.5): self.assertEqual(self.name(report(low=low)), "INCONCLUSIVE")
    self.assertIn("below 0.95", verdict.verdict_from_replay(report(low=0.85))[1])
  def test_learned_model(self):  # the model only turns an INCONCLUSIVE into a verdict; every other row is decided without it
    name, why = verdict.verdict_from_replay(report(k=100, **model()))
    self.assertEqual(name, "DETERMINISTIC_LEARNED")
    self.assertIn("no readable rule", why)
    self.assertEqual(self.name(report(k=100, **model(low=0.95))), "DETERMINISTIC_LEARNED")
    self.assertEqual(self.name(report(k=100, **model(low=0.9499))), "INCONCLUSIVE")
    self.assertEqual(self.name(report(k=100, **model(low=0.97, base=0.96))), "INCONCLUSIVE")  # no better than always answering the commonest output
    self.assertEqual(self.name(report(n=59, k=40, **model(n=59, k=59))), "INCONCLUSIVE")
    self.assertEqual(self.name(report(low=0.98, **model(low=0.0))), "DETERMINISTIC")
    self.assertEqual(self.name(report(low=0.85, **model())), "DETERMINISTIC_LEARNED")
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
      built = verdict.build(state, None, None, None)
      self.assertEqual((built["verdict"], built["exit_stage"]), (name, stage))
      self.assertEqual("conflict_pairs" in built, name == "PROBABILISTIC")
  def test_inconclusive_when_no_input_repeats_and_nothing_was_compared(self):  # a complete cross product (UCI Car) has no neighbour to disagree with
    near = {"compared": 0, "neighbour_disagreement": None, "chance": None, "closest_differing_pairs": []}
    state = {**self.state, "conflicts": {"near": near}}
    built = verdict.build(state, self.receipt(k=100), "rule", None)
    self.assertEqual(built["verdict"], "INCONCLUSIVE")
    self.assertNotIn("Diagnostic", built["rationale"])
    near = {**near, "compared": 40, "neighbour_disagreement": 0.25, "chance": 0.5}
    self.assertIn("25%", verdict.build({**self.state, "conflicts": {"near": near}}, self.receipt(k=100), "rule", None)["rationale"])
  def test_no_replay(self):
    self.assertEqual(verdict.build(self.state, None, "rule", None)["needs"], "replay")
  def test_rule_changed_after_replay(self):
    self.assertEqual(verdict.build(self.state, self.receipt(), "edited", None)["verdict"], "INCONCLUSIVE")
    self.assertEqual(verdict.build(self.state, self.receipt(), "rule", None)["verdict"], "DETERMINISTIC")
  def test_scope(self):
    asked = {**self.state, "triage": {"scope": "full", "scope_question": "q"}}
    self.assertEqual(verdict.build(asked, self.receipt(), "rule", None)["needs"], "scope")
    built = verdict.build(asked, self.receipt(), "rule", "decision_step_only")
    self.assertEqual(built["scope"], "decision_step_only")
    self.assertIn("extraction step", built["rationale"])
    self.assertNotIn("scope", verdict.build(asked, self.receipt(k=100), "rule", None))  # no deterministic claim, no scope needed

class TestReport(unittest.TestCase):  # the owner reads the answer and the tokens line; the evidence is printed only for whoever asks why
  def test_every_verdict_has_an_answer(self):
    self.assertEqual(set(verdict.ANSWERS), {"DETERMINISTIC", "DETERMINISTIC_LEARNED", "CONSTANT", "PROBABILISTIC", "INCONCLUSIVE", "OUT_OF_SCOPE"})
  def test_the_answer_says_yes_no_or_that_it_cannot_tell(self):
    for name in ("DETERMINISTIC", "DETERMINISTIC_LEARNED", "CONSTANT"): self.assertTrue(verdict.ANSWERS[name].startswith("Yes"), name)
    self.assertEqual(verdict.ANSWERS["DETERMINISTIC"], "Yes, deterministic.")
    self.assertTrue(verdict.ANSWERS["PROBABILISTIC"].startswith("No"))
    self.assertTrue(verdict.ANSWERS["INCONCLUSIVE"].startswith("Cannot tell"))
    self.assertNotIn("not deterministic", verdict.ANSWERS["INCONCLUSIVE"].lower())  # a weak rule is never evidence of a probabilistic loop
  def conclusion(self, state, receipt, **kwargs):
    # the lines `conclude` prints in a working directory holding this audit, receipt and rule
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
      os.mkdir("work")
      Path("work/audit.json").write_text(json.dumps(state))
      Path("Rule.lean").write_text("rule")
      if receipt: Path("work/receipt.json").write_text(json.dumps(receipt))
      out = io.StringIO()
      with contextlib.redirect_stdout(out): verdict.conclude(**kwargs)
      return out.getvalue().splitlines()
  def test_every_verdict_prints_the_header_the_answer_the_tokens_line_and_where_the_report_is(self):
    ruled = {"triage": {"scope": "full"}, "fit": {"rule_sha256": stats.sha256("rule")}}
    receipt = lambda **more: {"rule_sha256": stats.sha256("rule"), **report(**more)}  # noqa: E731
    exits = lambda name, stage: {"proposed": {"verdict": name, "exit_stage": stage, "why": "w"}, "conflicts": {"example_pairs": [["a", "b"]]}}  # noqa: E731
    for name, state, held in (("DETERMINISTIC", ruled, receipt()), ("DETERMINISTIC_LEARNED", ruled, receipt(k=100, **model())),
                              ("INCONCLUSIVE", ruled, receipt(k=100)), ("PROBABILISTIC", exits("PROBABILISTIC", "conflicts"), None),
                              ("OUT_OF_SCOPE", exits("OUT_OF_SCOPE", "triage"), None)):
      tokens, ask = verdict.tokens_line({"known": False}), [verdict.ASK] * (name in verdict.SCOPED)
      shown = self.conclusion(state, held)
      self.assertEqual(shown[1:-1], [f"Answer: {verdict.ANSWERS[name]}", tokens, *ask], name)
      self.assertTrue(shown[0].startswith(f"replaceable · {name}") and shown[-1].startswith("Full report: report.html"), name)
      why = self.conclusion(state, held, why=True)  # asked why: the verdict, reasons and tokens come first; the compact block still ends it
      self.assertEqual(why[-len(shown) + 1:], shown[1:], name)
      self.assertTrue(any(line.startswith(f"VERDICT: {name}") for line in why) and any(line.startswith("WHY:") for line in why), name)
  def test_only_a_loop_that_could_be_replaced_is_asked_about_its_spend(self):
    for name in verdict.ANSWERS:
      shown = page.terminal({"verdict": name, "tokens": {"known": False}})
      self.assertEqual("tinyailabs.com" in shown, name in verdict.SCOPED, name)
  def test_the_verdict_describes_the_loop_and_its_rules_in_the_owners_words(self):
    rules = [{"when": "derived0 is above 5", "then": "approve", "share": 0.5}]
    fit = {"rule_sha256": stats.sha256("rule"), "rules": rules, "derived": {"expressions": {"derived0": "x.a * x.b"}}}
    state = {"triage": {"scope": "full"}, "fit": fit}
    schema = {"input": [{"name": "a", "role": "decision", "type": "nat"}, {"name": "id", "role": "identifier", "type": "nat"}],
              "output": {"name": "output", "values": ["approve", "deny"]}}
    with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
      os.mkdir("work")
      for name, text in (("work/audit.json", json.dumps(state)), ("Rule.lean", "rule"), ("schema.json", json.dumps(schema)),
                         ("work/receipt.json", json.dumps({"rule_sha256": stats.sha256("rule"), **report()}))): Path(name).write_text(text)
      result = verdict.write()
    self.assertEqual(result["loop"], {"reads": ["a"], "answers": ["approve", "deny"]})  # an identifier is not something the loop decides on
    self.assertEqual(result["rules"], [{"when": "a * b is above 5", "then": "approve", "share": 0.5}])
  def test_the_answer_says_which_part_of_the_log_it_checked(self):
    plain = {"verdict": "DETERMINISTIC"}
    self.assertEqual(verdict.answer_line(plain), "Answer: Yes, deterministic.")
    settled = {**plain, "learning": {"converged": True, "runs": 6000, "from_run": 1300}}
    self.assertEqual(verdict.answer_line(settled),
                     "Answer: Yes, deterministic. Checked on the runs after the loop held 95% accuracy (after run 1,300 of 6,000).")
    always = {**plain, "learning": {"converged": True, "runs": 600}}  # right from the start: nothing was set aside
    self.assertEqual(verdict.answer_line(always), "Answer: Yes, deterministic.")
    never = {"verdict": "PROBABILISTIC", "learning": {"converged": False, "runs": 900, "accuracy": 0.76, "examined_from": 450}}
    self.assertEqual(verdict.answer_line(never),
                     "Answer: No, not deterministic. Its feedback holds at 76%, below 95%: its steady stretch was checked.")
  def test_the_learning_phase_is_priced_against_the_converged_loop(self):
    spend = {"known": True, "per_run": 1000.0, "logged": 300_000, "cases_with_tokens": 300,
             "learning": {"from_run": 1300, "accuracy_before": 0.85, "tokens": {"with_tokens": 1300, "mean": 2500.0}}}
    self.assertIn("the first 1,300 runs, before it converged (85% accurate), cost 2,500 per run, 2.5x as much", verdict.tokens_line(spend))
    thin = {**spend, "learning": {**spend["learning"], "tokens": {"with_tokens": 10, "mean": 9000.0}}}
    self.assertNotIn("before it converged", verdict.tokens_line(thin))  # too few runs to compare
    self.assertNotIn("before it converged", verdict.tokens_line({**spend, "learning": None}))
    self.assertIn("over its 300 runs after it converged", verdict.tokens_line(spend))
    self.assertNotIn("before it converged", verdict.tokens_line({**spend, "cases_with_tokens": 10}))  # the converged side is too short to compare too
  def test_the_token_block_carries_the_learning_phase_to_the_line(self):
    state = {"tokens": {"with_tokens": 5, "total": 500, "mean": 100.0}, "log_tokens": {"with_tokens": 60, "total": 6000, "mean": 100.0},
             "learning": {"converged": True, "runs": 500, "from_run": 40, "accuracy_before": 0.7, "tokens": {"with_tokens": 40, "mean": 300.0}}}
    block = verdict.token_block(state, None, {"verdict": "DETERMINISTIC"})
    self.assertEqual(block["learning"]["from_run"], 40)
    self.assertIn("the first 40 runs, before it converged (70% accurate), cost 300 per run, 3.0x as much", verdict.tokens_line(block))
  def test_a_moving_loop_is_inconclusive_whatever_the_exam_says(self):
    unsettled = {"recent": 0.92, "before": 0.86, "runs": 200}
    ruled = {"triage": {"scope": "full"}, "fit": {"rule_sha256": stats.sha256("rule")}, "learning": {"unsettled": unsettled}}
    said = verdict.build(ruled, {"rule_sha256": stats.sha256("rule"), **report()}, "rule", None)
    self.assertEqual((said["verdict"], said["exit_stage"]), ("INCONCLUSIVE", "feedback"))
    self.assertIn("it changed 200 runs before the end of the log (92% right since, 86% before)", said["rationale"])
    moving = {"verdict": "INCONCLUSIVE", "learning": {"converged": False, "runs": 900, "unsettled": unsettled}}
    self.assertEqual(verdict.answer_line(moving), "Answer: Cannot tell from this log. Its feedback has not settled: it changed 200 runs before "
                     "the end of the log (92% right since, 86% before). Run this again once it has been steady for 300 or more runs.")
  def test_a_figure_below_the_bar_is_never_rounded_up_to_it(self):
    self.assertEqual((verdict.pct(0.949), verdict.pct(0.95), verdict.pct(1.0)), ("94%", "95%", "100%"))
  def test_after_max_rows_the_report_says_only_the_latest_runs_were_examined(self):
    trimmed = {"converged": True, "runs": 6000, "from_run": 1300, "trimmed": True}
    self.assertTrue(verdict.answer_line({"verdict": "DETERMINISTIC", "learning": trimmed}).endswith(" (--max-rows: only its latest runs.)"))
    spent = {"with_tokens": 1300, "mean": 2500.0}
    whole = {"with_tokens": 1000, "total": 1_000_000, "mean": 1000.0}
    state = {"log_tokens": whole, "learning": {**trimmed, "accuracy_before": 0.7, "tokens": spent}}
    block = verdict.token_block(state, None, {"verdict": "DETERMINISTIC"})
    self.assertTrue(block["learning"]["trimmed"])
    self.assertIn("over its 1,000 latest runs", verdict.tokens_line(block))
  def test_a_moving_loop_does_not_hide_that_the_output_is_free_text(self):
    moving = {"unsettled": {"recent": 0.9, "before": 0.7, "runs": 200}}
    exits = lambda name: {"triage": {"scope": "full"}, "proposed": {"verdict": name, "exit_stage": "x", "why": "w"},  # noqa: E731
                          "conflicts": {"example_pairs": []}, "learning": moving}
    self.assertEqual(verdict.build(exits("OUT_OF_SCOPE"), None, None, None)["verdict"], "OUT_OF_SCOPE")
    self.assertEqual(verdict.build(exits("PROBABILISTIC"), None, None, None)["verdict"], "INCONCLUSIVE")  # conflicts across a change prove nothing
  def test_an_early_exit_quotes_the_whole_log_not_the_half_it_audited(self):
    half, whole = {"with_tokens": 5, "total": 50, "mean": 10.0}, {"with_tokens": 10, "total": 100, "mean": 10.0}
    block = verdict.token_block({"tokens": half, "log_tokens": whole}, None, {"verdict": "PROBABILISTIC"})
    self.assertEqual((block["cases_with_tokens"], block["logged"]), (10, 100))
    self.assertIn("(the log examined)", block["arithmetic"])
  def test_without_a_token_count_the_line_says_so_and_asks(self):
    self.assertEqual(verdict.tokens_line({"known": False}), "Tokens: not in this log.")
  def test_with_a_token_count_the_line_quotes_the_spend(self):
    spend = {"known": True, "per_run": 864.5, "logged": 518_700, "cases_with_tokens": 600}
    line = verdict.tokens_line(spend)
    self.assertEqual(line, "Tokens: 864 per run, 518,700 over its 600 logged runs.")
    self.assertNotIn("$", line)  # no price, no cost: nothing is assumed
    month = verdict.tokens_line({**spend, "per_month": 1_120_000}, usd=3)
    self.assertEqual(month, "Tokens: 864 per run, about 1,120,000 a month (about $3.36 at $3 per million tokens).")
    self.assertIn("(about $1.56 at $3 per million tokens)", verdict.tokens_line(spend, usd=3))
  def test_the_conclusion_quotes_the_logged_tokens_and_the_price_it_was_given(self):
    ruled = {"triage": {"scope": "full"}, "fit": {"rule_sha256": stats.sha256("rule")}, "tokens": stats.token_stats([{"tokens": 1000}] * 100),
             "runs_per_day": 10.0}
    held = {"rule_sha256": stats.sha256("rule"), "tokens": stats.token_stats([{"tokens": 1200}] * 100), **report()}
    line = [shown for shown in self.conclusion(ruled, held, usd=2) if shown.startswith("Tokens:")][0]
    self.assertEqual(line, "Tokens: 1,100 per run, about 330,000 a month (about $0.66 at $2 per million tokens).")

class TestTokenBlock(unittest.TestCase):
  def block(self, name, early=None, receipt=None, **more): return verdict.token_block({"tokens": early}, receipt, {"verdict": name, **more})
  def test_block_arithmetic(self):
    early, held = stats.token_stats([{"tokens": 1000}] * 100), stats.token_stats([{"tokens": 1200}] * 100)
    receipt = {"n_heldout": 100, "tokens": held}
    full = self.block("DETERMINISTIC", early, receipt)
    keys = ("per_run", "logged", "cases_with_tokens", "rule_share", "saved_per_run", "saved_logged")
    self.assertEqual([full[k] for k in keys], [1100.0, 220_000, 200, 1.0, 1100.0, 220_000])
    self.assertIn("saved over the log = 1.0 x 220000 = 220000", full["arithmetic"])
    self.assertEqual(self.block("CONSTANT", early, receipt)["rule_share"], 1.0)
    for name in ("INCONCLUSIVE", "PROBABILISTIC", "OUT_OF_SCOPE"):
      self.assertEqual([self.block(name, early)[k] for k in ("saved_logged", "logged")], [0, 100_000])
    self.assertIn("upper bound", self.block("DETERMINISTIC", early, receipt, scope="decision_step_only")["note"])
    self.assertNotIn("note", full)
  def test_a_monthly_pace_needs_runs_per_day(self):
    early, receipt = stats.token_stats([{"tokens": 1000}] * 100), {"n_heldout": 100, "tokens": stats.token_stats([{"tokens": 1200}] * 100)}
    paced = verdict.token_block({"tokens": early, "runs_per_day": 10.0}, receipt, {"verdict": "DETERMINISTIC"})
    self.assertEqual(paced["per_month"], 330_000)  # 1100 per run x 10 runs a day x 30 days
    self.assertNotIn("per_month", self.block("DETERMINISTIC", early, receipt))
  def test_unknown_without_counts(self):
    for state_tokens, receipt in ((None, None), (stats.token_stats([{}]), {"n_heldout": 100})):
      self.assertEqual(self.block("DETERMINISTIC", state_tokens, receipt)["known"], False)
  def test_tokens_do_not_reach_the_verdict(self):
    state, receipt = {"triage": {"scope": "full"}, "fit": {}}, {"rule_sha256": stats.sha256("rule"), **report()}
    counted = ({**state, "tokens": stats.token_stats([{"tokens": 5}])}, {**receipt, "tokens": stats.token_stats([{"tokens": 9}])})
    self.assertEqual(verdict.build(*counted, "rule", None), verdict.build(state, receipt, "rule", None))

if __name__ == "__main__": unittest.main()
