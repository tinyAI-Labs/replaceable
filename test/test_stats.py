# the bound, the baseline, the conflict scan and the token counts
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import stats

def rows(pairs): return [{"case_id": f"c{i}", "typed_input": {"x": x}, "output": o} for i, (x, o) in enumerate(pairs)]

class TestBound(unittest.TestCase):
  def test_values(self):
    for k, n, want in ((60, 60, 0.9513), (1200, 2000, 0.5816), (750, 1500, 0.4784)):
      self.assertEqual(round(stats.clopper_pearson_lower(k, n), 4), want)
  def test_edges(self):
    self.assertEqual(stats.clopper_pearson_lower(0, 50), 0.0)
    self.assertEqual(stats.clopper_pearson_lower(5, 0), 0.0)
    self.assertLess(stats.clopper_pearson_lower(20_000, 20_000), 1.0)  # large n neither overflows nor reaches certainty
  def test_baseline(self):
    self.assertEqual(stats.baseline(["a", "b", "a", "a"], ["b", "a"]), ("a", 0.75))
    self.assertEqual(stats.baseline(["a", "b"], ["b", "a"])[0], "b")  # ties break by the schema's order
    self.assertTrue(stats.beats_baseline(0.98, 0.96))  # a 96% majority needs L >= 0.98
    self.assertFalse(stats.beats_baseline(0.9799, 0.96))

class TestConflicts(unittest.TestCase):
  def reading(self, pairs): return stats.conflict_reading(stats.conflict_scan(rows(pairs), ["x"], ["a", "b"]))[0]
  def test_conflict_readings(self):
    self.assertEqual(self.reading([(1, "a"), (1, "b"), (2, "a"), (2, "b")] * 5), "conflicts")
    self.assertEqual(self.reading([(1, "a"), (1, "b")] * 5 + [(2, "a")] * 10), "one_group")
    self.assertEqual(self.reading([(1, "a")] * 19 + [(1, "b")] + [(2, "b")] * 20), "noise")  # 1 of 40 repeated cases: at or under 5%
    self.assertEqual(self.reading([(i, "a") for i in range(40)]), "uninformative")
  def test_conflict_rate_counts_repeated_cases_only(self):
    scan = stats.conflict_scan(rows([(1, "a"), (1, "b")] + [(i, "a") for i in range(2, 10)]), ["x"], ["a", "b"])
    self.assertEqual((scan["conflict_rate"], scan["repeat_share"], scan["conflicted_groups"]), (0.5, 0.2, 1))

class TestSample(unittest.TestCase):  # one Lean run takes a bounded number of cases, whatever the log size
  def test_small_logs_are_whole(self):
    few = rows([(i, "a") for i in range(100)])
    self.assertEqual(stats.sample(few, 100), few)
  def test_big_logs_are_cut_to_the_cap_by_case_id_alone(self):
    big = rows([(i, "a" if i % 2 else "b") for i in range(10_000)])
    kept = stats.sample(big, 1000)
    self.assertEqual(len(kept), 1000)
    self.assertEqual(kept, stats.sample(big, 1000))  # the same cases every time
    flipped = [{**r, "output": "c"} for r in big]  # outputs and inputs play no part in the choice
    self.assertEqual([r["case_id"] for r in kept], [r["case_id"] for r in stats.sample(flipped, 1000)])
    self.assertNotEqual([r["case_id"] for r in kept[:50]], [r["case_id"] for r in big[:50]])  # not simply the oldest cases

class TestTokens(unittest.TestCase):
  def test_row_tokens(self):
    for line, want in (({"tokens": 12}, 12), ({"usage": {"input_tokens": 10, "output_tokens": 5}}, 15), ({"input_tokens": 1, "output_tokens": 5}, 6),
                       ({"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}, 10), ({"tokens": 0}, 0),
                       ({"usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 99, "cache_creation_input_tokens": 1}}, 115),
                       ({"tokens": 12, "usage": {"total_tokens": 99}}, 12), ({}, None), ({"tokens": "12"}, None), ({"tokens": True}, None),
                       ({"tokens": -1}, None), ({"tokens": 1.5}, None), ({"usage": "n/a"}, None), ({"form_text": "x" * 400}, None)):
      self.assertEqual(stats.row_tokens(line), want, line)
  def test_token_stats(self):
    self.assertEqual(stats.token_stats([{"tokens": t} for t in range(1, 11)] + [{}]),
                     {"cases": 11, "with_tokens": 10, "total": 55, "mean": 5.5, "median": 5.5, "p90": 9})
    self.assertEqual(stats.token_stats([{}, {}]), {"cases": 2, "with_tokens": 0})

class TestConvergedFrom(unittest.TestCase):  # where a self-optimizing loop settles: how many runs were its learning phase
  ramp = [i % 5 >= 2 for i in range(300)]  # 60% right while it learns
  def test_a_loop_that_learns_and_holds_has_its_learning_phase_cut_off(self):
    self.assertEqual(stats.converged_from(self.ramp + [True] * 700), 300)
    self.assertEqual(stats.converged_from([True] * 300 + [False] * 100 + [True] * 500), 400)  # a later dip moves the start behind it
  def test_the_runs_before_the_first_full_block_are_checked_too(self):
    self.assertEqual(stats.converged_from([False] * 99 + [True] * 1000), 99)  # 1099 runs: the odd 99 at the front are not waved through
  def test_a_loop_that_was_always_good_has_no_learning_phase(self):
    self.assertEqual(stats.converged_from([True] * 500), 0)
  def test_the_tail_as_a_whole_is_at_least_95_percent(self):
    log = self.ramp + [i % 25 < 23 for i in range(2000)] + [True] * 1000  # 92% for a long stretch, then clean: blocks at 92% pass the floor
    start = stats.converged_from(log)
    self.assertGreater(start, 300)
    self.assertGreaterEqual(sum(log[start:]) / len(log[start:]), 0.95)
  def test_no_tail_no_convergence(self):
    self.assertIsNone(stats.converged_from([i % 5 != 0 for i in range(3000)]))  # 80% and flat: it never reaches 95%
    self.assertIsNone(stats.converged_from([True] * 100))  # too short to tell
    self.assertIsNone(stats.converged_from([False] * 500 + [True] * 120))  # settled too recently: fewer than 150 clean runs
    self.assertIsNone(stats.converged_from([True] * 3000 + [False] * 60))  # it regressed at the end: the last block does not hold

class TestRunsPerDay(unittest.TestCase):  # the pace a token count is extrapolated at: only real timestamps say how long the log took
  def rows(self, *ts): return [{"case_id": f"c{i}", "ts": t} for i, t in enumerate(ts)]
  def test_iso_timestamps_give_runs_per_day(self):
    self.assertEqual(stats.runs_per_day(self.rows("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z")), 1.5)  # 3 runs in 2 days
  def test_a_counter_a_missing_or_an_odd_timestamp_gives_nothing(self):
    for ts in ((0, 1, 2), ("2026-01-01", "soon"), (None, None), ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00")):  # the last mixes zone and no zone
      self.assertIsNone(stats.runs_per_day(self.rows(*ts)), ts)
    self.assertIsNone(stats.runs_per_day([{"case_id": "c0"}, {"case_id": "c1"}]))
    self.assertIsNone(stats.runs_per_day([]))
  def test_under_a_day_is_too_short_to_extrapolate(self):
    self.assertIsNone(stats.runs_per_day(self.rows("2026-01-01T00:00:00Z", "2026-01-01T12:00:00Z")))

if __name__ == "__main__": unittest.main()
