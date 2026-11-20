# the solver recovers a known rule from its log, and derive offers the quantities a policy plausibly computes
import datetime, random, sys, unittest
from fractions import Fraction
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import derive, fit

def schema(*fields): return {"input": list(fields), "output": {"name": "output", "values": ["approve", "review", "deny"]}}
def rows(make, n=400):
  rng = random.Random(1)
  cases = [make(rng) for _ in range(n)]
  return [{"case_id": f"c{i}", "typed_input": x, "output": out} for i, (x, out) in enumerate(cases)]

class TestFit(unittest.TestCase):
  def test_recovers_two_thresholds_and_an_enum(self):
    def make(rng):
      x = {"amount": rng.randrange(8000), "tier": rng.choice(["gold", "basic"])}
      return x, "deny" if x["amount"] > 5000 else "review" if x["amount"] > 1000 and x["tier"] == "basic" else "approve"
    fields = ({"name": "amount", "type": "nat"}, {"name": "tier", "type": "enum", "values": ["gold", "basic"]})
    report, lean = fit.fit_report(rows(make), schema(*fields))
    self.assertEqual((report["early_agreement"], report["leaves"], report["residual"]), (1.0, 4, []))
    for part in ("x.amount <= 5000", "x.amount <= 1000", "x.tier = Tier.gold"): self.assertIn(part, lean)
  def test_the_main_rules_are_worded_one_clause_per_field(self):
    def make(rng):
      x = {"amount": rng.randrange(8000), "tier": rng.choice(["gold", "basic"])}
      return x, "deny" if x["amount"] > 5000 else "review" if x["amount"] > 1000 and x["tier"] == "basic" else "approve"
    fields = ({"name": "amount", "type": "nat"}, {"name": "tier", "type": "enum", "values": ["gold", "basic"]})
    rules = fit.fit_report(rows(make), schema(*fields))[0]["rules"]
    self.assertEqual([(r["when"], r["then"]) for r in rules], [
      ("amount is above 5,000", "deny"), ("amount is at most 5,000 and tier is gold", "approve"),
      ("amount is 1,001 to 5,000 and tier is basic", "review"), ("amount is at most 1,000 and tier is basic", "approve")])
    self.assertAlmostEqual(sum(r["share"] for r in rules), 1.0, places=2)
  def test_the_wording_of_a_path_holds_for_the_same_inputs_as_the_path(self):
    # property: for random paths over every test kind, the sentence is true of exactly the inputs the path sends to the leaf
    names = ["red", "green", "blue", "grey"]
    schema_ = schema({"name": "n", "type": "nat"}, {"name": "c", "type": "enum", "values": names},
                     {"name": "d", "type": "nat", "unit": "calendar date, YYYYMMDD"})
    rng = random.Random(5)
    def holds(clause, x):
      for name in ("n", "c", "d"):
        if not clause.startswith(name + " "): continue
        rest = clause[len(name) + 1:]
        val = x[name]
        if rest.startswith("is none of "): return val not in rest[11:].split(", ")
        if rest.startswith("is one of "): return val in rest[10:].split(", ")
        if rest.startswith("is not "): return val != rest[7:]
        if rest.startswith("has no value"): return False
        if rest.startswith("is "):
          word = rest[3:]
          if name == "c": return val == word
          num = lambda s: int(s.replace(",", "").replace("-", ""))  # noqa: E731
          if word.startswith(("above ", "after ")): return val > num(word.split(" ", 1)[1])
          if word.startswith(("below ", "before ")): return val < num(word.split(" ", 1)[1])
          if word.startswith(("at most ", "on or before ")): return val <= num(word.rsplit(" ", 1)[1])
          if " to " in word:
            lo, hi = word.split(" to ")
            return num(lo) <= val <= num(hi)
          return val == num(word)
      raise AssertionError(clause)
    for _ in range(300):
      path = []
      for _ in range(rng.randrange(0, 4)):
        kind = rng.choice(["le", "le", "is", "in", "switch"])
        if kind == "le": test = fit.Test("le", rng.choice("nd"), thr=Fraction(rng.randrange(0, 40)))
        elif kind == "is": test = fit.Test("is", "c", value=rng.choice(names))
        elif kind == "in": test = fit.Test("in", "c", values=tuple(rng.sample(names, 2)))
        else: test = fit.Test("switch", "c", values=tuple(names))
        path.append((test, rng.randrange(test.arity)))
      text = fit.plain_when(path, schema_)
      for _ in range(40):
        x = {"n": rng.randrange(0, 45), "c": rng.choice(names), "d": 20240100 + rng.randrange(0, 45)}
        truth = all(test.branch(x) == branch for test, branch in path)
        clauses = [] if text == "everywhere" else text.split(" and ")
        self.assertEqual(all(holds(c, x) for c in clauses), truth, (text, x))
  def test_dates_are_not_compared_as_ratios(self):  # 20271231 / 20280101 is not a day: a ratio of two YYYYMMDD numbers is not calendar arithmetic
    start = datetime.date(2026, 1, 1)
    def make(rng):
      asked = start + datetime.timedelta(days=rng.randrange(700))
      expires = asked + datetime.timedelta(days=rng.randrange(5, 60))
      day = {"asked": int(asked.strftime("%Y%m%d")), "expires": int(expires.strftime("%Y%m%d"))}
      return day, "approve" if (expires - asked).days <= 30 else "deny"
    when = {"type": "nat", "unit": "calendar date as YYYYMMDD"}
    report, lean = fit.fit_report(rows(make, 2000), schema({"name": "asked", **when}, {"name": "expires", **when}))
    self.assertFalse([line for line in lean.splitlines() if "asked" in line and "expires" in line], lean)
  def test_noise_is_not_memorised(self):
    coin = rows(lambda rng: ({"n": rng.randrange(1000)}, rng.choice(["approve", "deny"])))
    report, _ = fit.fit_report(coin, schema({"name": "n", "type": "nat"}))
    self.assertLessEqual(report["leaves"], 3)  # a random answer earns no tree
    self.assertLess(report["early_agreement"], 0.7)

class TestDerive(unittest.TestCase):
  def test_candidates(self):
    def make(rng):
      day = datetime.date(2024, 1, 1) + datetime.timedelta(days=rng.randrange(365))
      return {"d": int(day.strftime("%Y%m%d")), "a": rng.randrange(1, 50), "b": rng.randrange(1, 50)}, "approve"
    fields = [{"name": "d", "type": "nat", "unit": "calendar date as YYYYMMDD"}, {"name": "a", "type": "nat"}, {"name": "b", "type": "nat"}]
    found = set(derive.candidates(rows(make), schema(*fields)).values())
    self.assertEqual(found, {"month of d", "day of the month of d", "weekday of d (0 = Monday, 6 = Sunday)",
                             "a times b", "a minus b (0 when negative)", "b minus a (0 when negative)"})

if __name__ == "__main__": unittest.main()
