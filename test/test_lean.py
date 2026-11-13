# the contract a rule must meet before Lean runs it, and the receipt that makes the replay one-shot
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "replaceable" / "scripts"))
import lean, replay, stats

FIELDS = [{"name": "amount", "type": "nat"}, {"name": "vip", "type": "bool"}, {"name": "tier", "type": "enum", "values": ["gold", "basic"]}]
SCHEMA = {"input": FIELDS, "output": {"name": "output", "values": ["approve", "deny"]}}

def report(n=200, k=200, base=0.5):
  lower = round(stats.clopper_pearson_lower(k, n), 4)
  return {"n_heldout": n, "n_match": k, "agreement": round(k / n, 4), "lower_bound": lower, "baseline": base, "all_outputs_constant": False}

class TestRuleContract(unittest.TestCase):
  def test_banned_tokens(self):
    for token in lean.BANNED_TOKENS: self.assertEqual(lean.scan_rule(f"def f (x : Input) : Output := {token}"), [f"banned:{token}"], token)
    self.assertEqual(lean.scan_rule("def f (x : Input) : Output := .approve\n#eval f"), ["banned:#eval"])
  def test_clean_rule(self):
    clean = "-- sorry, no import here\ndef f (x : Input) : Output :=\n  if x.amount <= 100 then Output.approve else Output.deny\n/- main -/\n"
    self.assertEqual(lean.scan_rule(clean), [])
    self.assertEqual(lean.scan_rule("def f (x : Input) : Output := x.main"), [])  # a field access is not the identifier `main`
    # with a string literal in the rule the scan stays conservative
    self.assertEqual(lean.scan_rule('def f (x : Input) : Output := g "-- sorry"'), ["banned:sorry"])
  def test_redefined_types_and_lookup_tables(self):
    self.assertEqual(lean.scan_rule("structure Input where\n  a : Nat"), ["redefines:Input"])
    self.assertEqual(lean.scan_rule("def f : Input -> Output\n" + "  | 1 => .approve\n" * 26), ["lookup_suspect"])
    self.assertEqual(lean.scan_rule("def f : Input -> Output\n" + "  | 1 => .approve\n" * 25), [])
    self.assertEqual(lean.scan_rule("def f (x : Input) : Output := .approve -- " + "x" * lean.MAX_RULE_BYTES), ["lookup_suspect"])
  def test_render_types(self):
    self.assertEqual(lean.render_types(SCHEMA), "inductive Tier | gold | basic\n  deriving Repr, DecidableEq\n\n"
                     "structure Input where\n  amount : Nat\n  vip : Bool\n  tier : Tier\n  deriving Repr, DecidableEq\n\n"
                     "inductive Output | approve | deny\n  deriving Repr, DecidableEq\n")
    self.assertTrue(lean.render_types({"input": [], "output": {"values": []}}).startswith("-- free-text output"))
    for bad in ({"name": "def", "type": "nat"}, {"name": "has space", "type": "nat"}, {"name": "input", "type": "enum", "values": ["a"]}):
      with self.assertRaises(ValueError): lean.render_types({**SCHEMA, "input": [bad]})
  def test_literal(self):
    self.assertEqual(lean.literal(SCHEMA, {"amount": 7, "vip": False, "tier": "basic"}),
                     "({ amount := 7, vip := false, tier := Tier.basic } : Input)")
    for bad in ({"amount": True}, {"amount": -1}, {"amount": 1.5}, {"vip": 1}, {"tier": "silver"}):
      with self.assertRaises(ValueError): lean.literal(SCHEMA, {"amount": 7, "vip": False, "tier": "basic", **bad})

class TestReceipt(unittest.TestCase):
  def test_second_replay_is_refused(self):
    with tempfile.TemporaryDirectory() as tmp:
      first = {"rule_sha256": "abc", **report()}
      (Path(tmp) / stats.RECEIPT_FILE).write_text(json.dumps(first))
      again = replay.replay_once("", "def f (x : Input) : Output := .approve", SCHEMA, [], [], "sha", Path(tmp))  # refused before Lean is needed
      self.assertEqual(again, {"error": "replay already used: the first result stands", "receipt": first})
  def test_read_receipt(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / stats.RECEIPT_FILE
      self.assertIsNone(replay.read_receipt(Path(tmp)))
      path.write_text("{not json")
      self.assertEqual(replay.read_receipt(Path(tmp)), {"failure": "receipt_unreadable"})
      path.write_text(json.dumps({"rule_sha256": "abc"}))  # the process died between claiming the replay and its result
      self.assertEqual(replay.read_receipt(Path(tmp))["failure"], "replay_interrupted")
  def test_heldout_is_refused_as_a_fitting_log(self):
    with tempfile.TemporaryDirectory() as tmp:
      held, copy = Path(tmp) / "heldout.jsonl", Path(tmp) / "copy.jsonl"
      for path in (held, copy): path.write_text('{"a": 1}\n')
      for log in (held, copy, Path(tmp) / "elsewhere" / "heldout.jsonl"):  # the file, a byte-identical copy, any file of that name
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit): stats.read_early(log, held)

if __name__ == "__main__": unittest.main()
