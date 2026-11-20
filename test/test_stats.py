# the bound, the baseline, the conflict scan and the token counts
import random, sys, unittest
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

class TestSteadyFrom(unittest.TestCase):  # where a loop's feedback becomes steady: the tail after its latest change
  ramp = [i % 5 >= 2 for i in range(300)]  # 60% right while it learns
  def test_a_loop_that_learns_and_holds_is_cut_after_its_learning_phase(self):
    log = self.ramp + [True] * 700
    start = stats.steady_from(log)
    self.assertTrue(300 <= start <= 600, start)  # it may give up a few clean runs after the change, never keep an old one
    self.assertTrue(all(log[start:]))
  def test_a_loop_that_never_changed_is_steady_from_the_start(self):
    self.assertEqual(stats.steady_from([True] * 500), 0)
    self.assertEqual(stats.steady_from([i % 5 != 0 for i in range(2000)]), 0)  # 80% throughout
  def test_a_fix_in_the_last_runs_of_a_long_log_is_seen(self):
    log = [i % 25 < 23 for i in range(5750)] + [True] * 250  # 92% for 5750 runs, then 250 clean ones
    start = stats.steady_from(log)
    self.assertTrue(5750 <= start <= 5900, start)
  def test_a_dip_at_the_end_leaves_only_the_dip(self):
    self.assertGreaterEqual(stats.steady_from([True] * 3000 + [False] * 60), 2900)
  def test_noise_alone_rarely_looks_like_a_change(self):
    rng = random.Random(7)  # 200 flat loops at 81%
    changed = sum(stats.steady_from([rng.random() < 0.81 for _ in range(3000)]) > 0 for _ in range(200))
    self.assertLess(changed, 20)

class TestDrifting(unittest.TestCase):  # a slow slope inside a steady stretch
  def test_a_slow_steady_rise_is_a_drift(self):
    log = [(j * 7919) % 750 >= wrong for wrong in (129, 101, 83, 67) for j in range(750)]  # 82.8%, 86.5%, 88.9%, 91.1%, wrong runs scattered
    self.assertEqual(stats.steady_from(log), 0)  # no step for the scan to find
    first, last = stats.drifting(log)
    self.assertLess(first, 0.86)
    self.assertGreater(last, 0.89)
  def test_a_flat_loop_and_a_short_log_do_not_drift(self):
    self.assertIsNone(stats.drifting([i % 5 != 0 for i in range(3000)]))
    self.assertIsNone(stats.drifting([True] * 100 + [False] * 40))  # too short to split in thirds of 50

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
