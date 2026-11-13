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

if __name__ == "__main__": unittest.main()
