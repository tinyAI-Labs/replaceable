# NOTE: written policies use round numbers, so a threshold is placed at the roundest value inside the gap the data leaves
from __future__ import annotations
from fractions import Fraction
from math import ceil, floor, inf

def roundness(m: int) -> tuple[int, bool]:
  # how round an integer is: its trailing zeros, then whether the remaining digits are a multiple of five
  if m == 0: return (99, True)
  zeros = len(str(m)) - len(str(m).rstrip("0"))
  return (zeros, (m // 10**zeros) % 5 == 0)

# True when `x < threshold + 1` reads rounder than `x <= threshold` (e.g. < 50000 rather than <= 49999)
def strict(threshold: int) -> bool: return roundness(threshold + 1) > roundness(threshold)

def round_integer(low: int, high: int) -> int:
  # the roundest integer in [low, high]: a multiple of the largest power of ten, preferring multiples of five of it
  power = 10 ** len(str(high))
  while power > 1 and ceil(low / power) * power > high: power //= 10
  first = ceil(low / power) * power
  return next((m for m in range(first, high + 1, power) if (m // power) % 5 == 0), first)

def simplest_fraction(low: Fraction, high: Fraction | float, with_low: bool = True, with_high: bool = False) -> Fraction:
  # NOTE: the fraction with the smallest denominator between `low` and `high` (by default `low` included, `high` not), for 0 <= low < high,
  # by continued fractions: an integer in the interval is the answer; otherwise both ends share their whole part, and the answer is
  # that whole part plus one over the simplest fraction between the reciprocals of the rests.
  whole = floor(low)
  if low == whole and with_low: return Fraction(whole)
  if whole + 1 < high or (whole + 1 == high and with_high): return Fraction(whole + 1)
  return whole + 1 / simplest_fraction(1 / (high - whole), 1 / (low - whole) if low > whole else inf, with_high, with_low)

def pick_threshold(kind: str, low: Fraction, high: Fraction | float) -> Fraction:
  # a threshold t with low <= t < high: every value up to `low` passes the test, every value from `high` fails
  if kind == "ratio":  # NOTE: no unit makes a ratio threshold round, so stay in the middle half of the gap
    if high == inf: return simplest_fraction(low, low + 1)
    quarter = (high - low) / 4
    return simplest_fraction(low + quarter, high - quarter)
  bottom = floor(low)
  top = bottom + 1_000_000_000 if high == inf else ceil(high) - 1
  at_most, below = round_integer(bottom, top), round_integer(bottom + 1, top + 1)  # `x <= at_most` or `x < below`
  return Fraction(below - 1 if roundness(below) > roundness(at_most) else at_most)
