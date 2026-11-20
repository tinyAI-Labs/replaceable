# the verdict table as code: every row, its boundaries, and the tokens block that must never move a verdict
import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import stats, verdict

def report(n=200, k=200, base=0.5, constant=False, low=None, **more):
  low = round(stats.clopper_pearson_lower(k, n), 4) if low is None else low
  return {"n_heldout": n, "n_match": k, "agreement": round(k / n, 4), "lower_bound": low, "baseline": base, "all_outputs_constant": constant, **more}

def model(n=200, k=200, base=0.5, low=None):  # what the learned model scored on the held-back cases, at the 99% bound
  low = round(stats.clopper_pearson_lower(k, n, 0.99), 4) if low is None else low
  return {"model": {"n": n, "n_match": k, "why": "A learned model reproduced them.", "baseline": base, "lower_bound": low}}

def fallback(inside=40, outside=160, misses_outside=0, bound=0.97):
  return {"fallback": {"inside": inside, "outside": outside, "misses_outside": misses_outside, "outside_lower_bound": bound}}

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
  def test_core_fallback(self):
    self.assertEqual(self.name(report(low=0.85, **fallback())), "DETERMINISTIC_CORE_FALLBACK")
    self.assertEqual(self.name(report(low=0.80, **fallback(bound=0.95))), "DETERMINISTIC_CORE_FALLBACK")
    for weak in (fallback(outside=59), fallback(inside=0), fallback(bound=0.9499), {}):
      self.assertEqual(self.name(report(low=0.85, **weak)), "INCONCLUSIVE")
    self.assertEqual(self.name(report(low=0.7999, **fallback())), "INCONCLUSIVE")
  def test_learned_model(self):  # the model only turns an INCONCLUSIVE into a verdict; every other row is decided without it
    name, why = verdict.verdict_from_replay(report(k=100, **model()))
    self.assertEqual(name, "DETERMINISTIC_LEARNED")
    self.assertIn("no readable rule", why)
    self.assertEqual(self.name(report(k=100, **model(low=0.95))), "DETERMINISTIC_LEARNED")
    self.assertEqual(self.name(report(k=100, **model(low=0.9499))), "INCONCLUSIVE")
    self.assertEqual(self.name(report(k=100, **model(low=0.97, base=0.96))), "INCONCLUSIVE")  # no better than always answering the commonest output
    self.assertEqual(self.name(report(n=59, k=40, **model(n=59, k=59))), "INCONCLUSIVE")
    self.assertEqual(self.name(report(low=0.98, **model(low=0.0))), "DETERMINISTIC")
    self.assertEqual(self.name(report(low=0.85, **fallback(), **model())), "DETERMINISTIC_CORE_FALLBACK")
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
    self.assertEqual(set(verdict.ANSWERS), {"DETERMINISTIC", "DETERMINISTIC_CORE_FALLBACK", "DETERMINISTIC_LEARNED", "CONSTANT", "PROBABILISTIC",
                                            "INCONCLUSIVE", "OUT_OF_SCOPE"})
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
  def test_every_verdict_prints_the_answer_and_the_tokens_line_and_nothing_else(self):
    ruled = {"triage": {"scope": "full"}, "fit": {"rule_sha256": stats.sha256("rule")}}
    receipt = lambda **more: {"rule_sha256": stats.sha256("rule"), **report(**more)}  # noqa: E731
    exits = lambda name, stage: {"proposed": {"verdict": name, "exit_stage": stage, "why": "w"}, "conflicts": {"example_pairs": [["a", "b"]]}}  # noqa: E731
    for name, state, held in (("DETERMINISTIC", ruled, receipt()), ("DETERMINISTIC_LEARNED", ruled, receipt(k=100, **model())),
                              ("INCONCLUSIVE", ruled, receipt(k=100)), ("PROBABILISTIC", exits("PROBABILISTIC", "conflicts"), None),
                              ("OUT_OF_SCOPE", exits("OUT_OF_SCOPE", "triage"), None)):
      tokens = verdict.tokens_line({"known": False}, ask=name in verdict.SCOPED)
      self.assertEqual(self.conclusion(state, held), [f"Answer: {verdict.ANSWERS[name]}", tokens], name)
      why = self.conclusion(state, held, why=True)  # asked why: the verdict, reasons and tokens come first; the answer and tokens lines still end it
      self.assertEqual(why[-2:], [f"Answer: {verdict.ANSWERS[name]}", tokens], name)
      self.assertTrue(any(line.startswith(f"VERDICT: {name}") for line in why) and any(line.startswith("WHY:") for line in why), name)
  def test_only_a_loop_that_could_be_replaced_is_asked_about_its_spend(self):
    for name in verdict.ANSWERS:
      self.assertEqual("tinyailabs.com" in verdict.tokens_line({"known": False}, ask=name in verdict.SCOPED), name in verdict.SCOPED, name)
    self.assertNotIn("tinyailabs.com", verdict.tokens_line({"known": True, "per_run": 9, "logged": 90, "cases_with_tokens": 10}, ask=False))
  def test_the_answer_says_which_part_of_the_log_it_checked(self):
    plain = {"verdict": "DETERMINISTIC"}
    self.assertEqual(verdict.answer_line(plain), "Answer: Yes, deterministic.")
    settled = {**plain, "learning": {"converged": True, "runs": 6000, "from_run": 1300}}
    self.assertEqual(verdict.answer_line(settled),
                     "Answer: Yes, deterministic. Checked on the runs after the loop reached 95% accuracy (from run 1,300 of 6,000).")
    always = {**plain, "learning": {"converged": True, "runs": 600}}  # right from the start: nothing was set aside
    self.assertEqual(verdict.answer_line(always), "Answer: Yes, deterministic.")
    never = {"verdict": "PROBABILISTIC", "learning": {"converged": False, "runs": 900, "accuracy": 0.76}}
    self.assertEqual(verdict.answer_line(never),
                     "Answer: No, not deterministic. The loop never settles at 95% accuracy (76%): this is about a loop that is still learning.")
  def test_the_learning_phase_is_priced_against_the_converged_loop(self):
    spend = {"known": True, "per_run": 1000.0, "logged": 300_000, "cases_with_tokens": 300,
             "learning": {"from_run": 1300, "accuracy_before": 0.85, "tokens": {"with_tokens": 1300, "mean": 2500.0}}}
    self.assertIn("the 1,300 runs before it converged (85% accurate) used 2,500 per run, 2.5x as much", verdict.tokens_line(spend))
    thin = {**spend, "learning": {**spend["learning"], "tokens": {"with_tokens": 10, "mean": 9000.0}}}
    self.assertNotIn("before it converged", verdict.tokens_line(thin))  # too few runs to compare
    self.assertNotIn("before it converged", verdict.tokens_line({**spend, "learning": None}))
  def test_an_early_exit_quotes_the_whole_log_not_the_half_it_audited(self):
    half, whole = {"with_tokens": 5, "total": 50, "mean": 10.0}, {"with_tokens": 10, "total": 100, "mean": 10.0}
    block = verdict.token_block({"tokens": half, "log_tokens": whole}, None, {"verdict": "PROBABILISTIC"})
    self.assertEqual((block["cases_with_tokens"], block["logged"]), (10, 100))
    self.assertIn("(the whole log)", block["arithmetic"])
  def test_without_a_token_count_the_line_says_so_and_asks(self):
    self.assertEqual(verdict.tokens_line({"known": False}),
                     "Tokens: not in this log. Do you want to reduce your token spend for this agentic loop to zero? Find out how: https://tinyailabs.com/")
  def test_with_a_token_count_the_line_quotes_the_spend(self):
    spend = {"known": True, "per_run": 864.5, "logged": 518_700, "cases_with_tokens": 600}
    line = verdict.tokens_line(spend)
    self.assertTrue(line.startswith("Tokens: this loop used 864 per run, 518,700 over its 600 logged runs"), line)
    self.assertNotIn("a month", line)  # no real timestamps, no pace
    self.assertNotIn("$", line)  # no price, no cost: nothing is assumed
    self.assertTrue(line.endswith("Do you want to reduce your token spend for this agentic loop to zero? Find out how: https://tinyailabs.com/"))
    month = verdict.tokens_line({**spend, "per_month": 1_120_000}, usd=3)
    self.assertIn("at that pace about 1,120,000 a month (about $3.36 a month at $3 per million tokens)", month)
    self.assertIn("(about $1.56 over the logged runs at $3 per million tokens)", verdict.tokens_line(spend, usd=3))
  def test_the_conclusion_quotes_the_logged_tokens_and_the_price_it_was_given(self):
    ruled = {"triage": {"scope": "full"}, "fit": {"rule_sha256": stats.sha256("rule")}, "tokens": stats.token_stats([{"tokens": 1000}] * 100),
             "runs_per_day": 10.0}
    held = {"rule_sha256": stats.sha256("rule"), "tokens": stats.token_stats([{"tokens": 1200}] * 100), **report()}
    line = self.conclusion(ruled, held, usd=2)[-1]
    self.assertIn("1,100 per run, 220,000 over its 200 logged runs", line)
    self.assertIn("at that pace about 330,000 a month (about $0.66 a month at $2 per million tokens)", line)

class TestTokenBlock(unittest.TestCase):
  def block(self, name, early=None, receipt=None, **more): return verdict.token_block({"tokens": early}, receipt, {"verdict": name, **more})
  def test_block_arithmetic(self):
    early, held = stats.token_stats([{"tokens": 1000}] * 100), stats.token_stats([{"tokens": 1200}] * 100)
    receipt = {"n_heldout": 100, "tokens": held, **fallback(inside=25, outside=75)}
    full = self.block("DETERMINISTIC", early, receipt)
    keys = ("per_run", "logged", "cases_with_tokens", "rule_share", "saved_per_run", "saved_logged")
    self.assertEqual([full[k] for k in keys], [1100.0, 220_000, 200, 1.0, 1100.0, 220_000])
    core = self.block("DETERMINISTIC_CORE_FALLBACK", early, receipt)
    self.assertEqual([core[k] for k in ("rule_share", "saved_per_run", "saved_logged")], [0.75, 825.0, 165_000])  # 75 of 100 held-back cases
    self.assertIn("saved over the log = 0.75 x 220000 = 165000", core["arithmetic"])
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
