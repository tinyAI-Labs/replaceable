# round thresholds inside the gap the data leaves
import sys, unittest
from fractions import Fraction
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import thresholds

class TestThresholds(unittest.TestCase):
  def test_round_integer(self):
    self.assertEqual(thresholds.round_integer(49_100, 50_900), 50_000)
    self.assertEqual(thresholds.round_integer(1_234, 1_239), 1_235)
    self.assertEqual(thresholds.round_integer(7, 7), 7)
  def test_simplest_fraction(self):
    self.assertEqual(thresholds.simplest_fraction(Fraction(2, 5), Fraction(3, 5)), Fraction(1, 2))
    self.assertEqual(thresholds.simplest_fraction(Fraction(1, 3), Fraction(1, 2)), Fraction(1, 3))  # the low end is included
    self.assertEqual(thresholds.simplest_fraction(Fraction(5, 2), Fraction(7, 2)), 3)
  def test_pick_threshold(self):
    self.assertEqual(thresholds.pick_threshold("le", Fraction(29_950), Fraction(30_020)), 30_000)
    self.assertEqual(thresholds.pick_threshold("le", Fraction(29_990), Fraction(30_000)), 29_999)  # rendered as `< 30000`
    self.assertTrue(thresholds.strict(29_999))
    self.assertEqual(thresholds.pick_threshold("ratio", Fraction(2, 5), Fraction(3, 5)), Fraction(1, 2))
    self.assertEqual(thresholds.pick_threshold("ratio", Fraction(5, 2), float("inf")), 3)

if __name__ == "__main__": unittest.main()
