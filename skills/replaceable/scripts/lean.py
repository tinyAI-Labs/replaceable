# the Lean rule contract: generated types, the static scan of a rule, bounded Lean runs
from __future__ import annotations
import os, re, signal, subprocess
from pathlib import Path
from typing import Any, NamedTuple, Sequence
from stats import Schema, emit

CHECK_TIMEOUT, RUN_TIMEOUT = 30, 60  # seconds; the first start of a fresh toolchain can be slow
LEAN_OUTPUT_LIMIT, MAX_LITERAL_ARMS = 1 << 20, 25
HARNESS_CHUNK = 40  # cases per generated Lean array, and arrays per generated call group
MAX_RULE_BYTES = 4096  # a rule is a few dozen branches at most; anything longer is a table of cases
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
ELAN_INSTALL = "curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y"

# whole-identifier matches over the rule without its comments: placeholders and escape hatches first; `instance` and `main` protect the harness
BANNED_TOKENS = ("sorry", "axiom", "admit", "partial", "unsafe", "opaque", "implemented_by", "extern", "native_decide", "IO", "run_cmd",
                 "include_str", "elab", "macro", "initialize", "import", "instance", "main")
_BANNED_RE = re.compile(r"(?<![\w.'])(" + "|".join(re.escape(t) for t in BANNED_TOKENS) + r")(?![\w'])")
_DIRECTIVE_RE = re.compile(r"#(?:eval|exit)")  # #exit would skip the harness appended after the rule
_REDEFINE_RE = re.compile(r"\b(?:structure|inductive|def|abbrev|class)\s+(Input|Output)\b")
_LITERAL_ARM_RE = re.compile(r'^\s*\|\s*(\d|")', re.MULTILINE)
_AXIOMS_RE = re.compile(r"'f' depends on axioms: \[(.*?)\]", re.DOTALL)
_FALLBACK_RE = re.compile(r"^def fallback\b", re.MULTILINE)

LEAN_RESERVED = {
  "def", "theorem", "example", "structure", "inductive", "class", "instance", "where", "with", "match", "fun", "let", "have", "show", "by", "do",
  "if", "then", "else", "end", "at", "from", "in", "open", "namespace", "section", "variable", "universe", "import", "deriving", "abbrev", "mutual",
  "unsafe", "partial", "private", "protected", "macro", "syntax", "notation", "return", "for", "while", "try", "catch", "true", "false", "Type",
  "Prop", "Sort", "this", "sorry", "of", "extends", "axiom", "opaque", "noncomputable", "attribute", "export", "local", "using", "calc", "nomatch",
  "nofun", "mut", "break", "continue", "unless", "finally", "initialize", "set_option", "termination_by", "decreasing_by"}
# type names an enum field must not produce (`pascal` of its name): the contract's own types and Lean's core ones
RESERVED_TYPES = {
  "Input", "Output", "Nat", "Int", "Bool", "String", "Char", "Float", "Unit", "List", "Array", "Option", "Fin", "Prod", "Sum", "Except", "Id", "Io",
  "Type", "Prop", "Sort", "True", "False", "And", "Or", "Not", "Eq", "Ne", "Iff", "Exists", "Decidable", "Repr", "Empty", "Subtype", "Function",
  "Task", "Thunk", "Name", "Main"}

def pascal(name: str) -> str: return "".join(part.capitalize() for part in name.split("_"))
def has_fallback(rule_src: str) -> bool: return bool(_FALLBACK_RE.search(rule_src))  # a rule claims a region with `def fallback : Input -> Bool`
def derived_name(k: int) -> str: return f"derived{k}"

def strip_comments(source: str) -> str:
  # NOTE: only where that is unambiguous. In a text with a string literal or a «quoted name» a comment marker can be data, so such a
  # text is returned unchanged and the scan stays conservative over all of it.
  if '"' in source or "«" in source: return source
  kept, i, depth = [], 0, 0
  while i < len(source):
    two = source[i:i + 2]
    if two == "/-": depth, i = depth + 1, i + 2
    elif depth: depth, i = (depth - 1, i + 2) if two == "-/" else (depth, i + 1)
    elif two == "--":
      end = source.find("\n", i)
      i = len(source) if end < 0 else end
    else:
      kept.append(source[i])
      i += 1
  return "".join(kept)

def _check_identifier(name: str):
  if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) and name not in LEAN_RESERVED: return
  raise ValueError(f"schema name {name!r} is not a usable Lean identifier")

def render_types(schema: Schema) -> str:
  # Types.lean: one enum per enum field, the `Input` structure and `Output`
  if not schema["output"]["values"]: return "-- free-text output: there is no finite Output type to declare\n"
  lines, fields, type_names = [], [], set()
  for f in schema["input"]:
    _check_identifier(f["name"])
    if f["type"] != "enum":
      fields.append(f"  {f['name']} : {'Nat' if f['type'] == 'nat' else 'Bool'}")
      continue
    if pascal(f["name"]) in RESERVED_TYPES | type_names:
      raise ValueError(f"enum field {f['name']!r} would declare the Lean type {pascal(f['name'])}, which is taken")
    type_names.add(pascal(f["name"]))
    for value in f["values"]: _check_identifier(value)
    lines.append(f"inductive {pascal(f['name'])} | {' | '.join(f['values'])}\n  deriving Repr, DecidableEq\n")
    fields.append(f"  {f['name']} : {pascal(f['name'])}")
  for value in schema["output"]["values"]: _check_identifier(value)
  body = "\n".join(fields)
  lines.append(f"structure Input where\n{body}\n  deriving Repr, DecidableEq\n")
  lines.append(f"inductive Output | {' | '.join(schema['output']['values'])}\n  deriving Repr, DecidableEq\n")
  return "\n".join(lines)

def scan_rule(rule_src: str) -> list[str]:
  # static violations: placeholders, escape hatches, redefined types, lookup tables of observed cases
  code = strip_comments(rule_src)
  violations = sorted({f"banned:{m}" for m in _BANNED_RE.findall(code)}) + sorted({f"banned:{m}" for m in _DIRECTIVE_RE.findall(code)})
  violations += sorted({f"redefines:{m}" for m in _REDEFINE_RE.findall(code)})
  if len(_LITERAL_ARM_RE.findall(code)) > MAX_LITERAL_ARMS or len(rule_src.encode()) > MAX_RULE_BYTES: violations.append("lookup_suspect")
  return violations

class LeanRun(NamedTuple):
  ok: bool
  output: str
  failure: str | None = None  # None, `timeout`, `output_limit`, `crash` or `lean_not_found`

def run_lean(source: str, workdir: Path, *, run: bool, timeout: int, lean: str = "lean", env: dict[str, str] | None = None) -> LeanRun:
  # write `source` to workdir/Check.lean and compile it (or `lean --run` it), bounded in time and output
  workdir.mkdir(parents=True, exist_ok=True)
  (workdir / "Check.lean").write_text(source, encoding="utf-8")
  try:  # NOTE: run output is stdout-only, so that what Lean says on stderr cannot be taken for a rule's answer
    proc = subprocess.Popen([lean, *(["--run"] if run else []), "Check.lean"], cwd=workdir, env=env, stdout=subprocess.PIPE, encoding="utf-8",
                            stderr=subprocess.DEVNULL if run else subprocess.STDOUT, errors="replace", start_new_session=True)
  except FileNotFoundError: return LeanRun(False, "", "lean_not_found")
  try: output, _ = proc.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()
    return LeanRun(False, "", "timeout")
  if len(output) > LEAN_OUTPUT_LIMIT: return LeanRun(False, output[:LEAN_OUTPUT_LIMIT], "output_limit")
  return LeanRun(proc.returncode == 0, output, None if proc.returncode == 0 else "crash")

def lean_version(lean: str = "lean", env: dict[str, str] | None = None) -> str | None:
  try: done = subprocess.run([lean, "--version"], capture_output=True, encoding="utf-8", errors="replace", timeout=CHECK_TIMEOUT, env=env)
  except (OSError, subprocess.TimeoutExpired): return None
  return done.stdout.strip() if done.returncode == 0 and "Lean" in done.stdout else None

def require_lean():
  # NOTE: a missing tool is reported as that and stops the run (exit 2): it is never a finding about the rule or the loop
  if lean_version() is not None: return
  emit({"error": "lean_not_found", "detail": f"`lean` (Lean 4) is not on PATH or does not start. Install it with: {ELAN_INSTALL}   then: "
        'export PATH="$HOME/.elan/bin:$PATH"   (other ways: https://lean-lang.org/install). Then run the same command again. '
        "Nothing was tested; this says nothing about the rule or the loop."})
  raise SystemExit(2)

class CheckResult(NamedTuple):
  compiled: bool
  violations: tuple[str, ...]

def check_rule(types_src: str, rule_src: str, workdir: Path, **lean: Any) -> CheckResult:
  # scan, then compile Types.lean + Rule.lean and verify the axioms `f` depends on
  violations = scan_rule(rule_src)
  if violations: return CheckResult(False, tuple(violations))
  typecheck = "def harnessTypecheckFallback : Input → Bool := fallback\n" if has_fallback(rule_src) else ""
  source = f"{types_src}\n{rule_src}\n\ndef harnessTypecheckF : Input → Output := f\n{typecheck}#print axioms f\n"
  run = run_lean(source, workdir, run=False, timeout=CHECK_TIMEOUT, **lean)
  if run.failure in ("timeout", "output_limit", "lean_not_found"): return CheckResult(False, (f"checker_{run.failure}",))
  if not run.ok: return CheckResult(False, ("compile_error: " + "\n".join(line for line in run.output.splitlines() if "error" in line)[:600],))
  match = _AXIOMS_RE.search(run.output)
  bad = sorted(({a.strip() for a in match.group(1).split(",")} if match else set()) - ALLOWED_AXIOMS)
  return CheckResult(not bad, tuple(f"axiom:{a}" for a in bad))

def literal(schema: Schema, typed: dict[str, Any]) -> str:
  # one typed input as a Lean structure literal, validated against the schema
  parts = []
  for f in schema["input"]:
    name, v, enum = f["name"], typed[f["name"]], f["type"] not in ("nat", "bool")
    if f["type"] == "nat" and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0): raise ValueError(f"{name}={v!r} is not a Nat")
    if f["type"] == "bool" and not isinstance(v, bool): raise ValueError(f"{name}={v!r} is not a Bool")
    if enum and v not in f["values"]: raise ValueError(f"{name}={v!r} is not one of {f['values']}")
    parts.append(f"{name} := {pascal(name)}.{v}" if enum else f"{name} := {str(v).lower()}")
  return "({ " + ", ".join(parts) + " } : Input)"

class RuleRun(NamedTuple):
  outputs: list[str] | None  # predicted outputs in input order, or None with a failure code
  failure: str | None = None  # `replay_timeout`, `output_limit` or `replay_failed`
  fallback: list[bool] | None = None  # per-input fallback flags, only when the rule defines `fallback`

def _harness(schema: Schema, inputs: Sequence[dict[str, Any]], line: str) -> str:
  # NOTE: Lean elaborates one huge literal or one huge `do` block recursively, so the cases go in as small arrays and the calls in
  # small groups: any log size compiles. Every input prints `line` (a String expression of `x`) prefixed with `@@`.
  literals = [literal(schema, t) for t in inputs]
  chunks = [literals[i:i + HARNESS_CHUNK] for i in range(0, len(literals), HARNESS_CHUNK)]
  arrays = "\n\n".join(f"def harnessInputs{k} : Array Input := #[\n  " + ",\n  ".join(chunk) + "]" for k, chunk in enumerate(chunks))
  groups = [range(i, min(i + HARNESS_CHUNK, len(chunks))) for i in range(0, len(chunks), HARNESS_CHUNK)]
  calls = ["\n".join(f"  harnessRun harnessInputs{k}" for k in group) for group in groups]
  parts = "\n\n".join(f"def harnessPart{g} : IO Unit := do\n{body}" for g, body in enumerate(calls))
  return ("def harnessRun (xs : Array Input) : IO Unit := do\n  for x in xs do\n" f'    IO.println ("@@" ++ {line})\n\n' f"{arrays}\n\n{parts}\n\n"
          "def main : IO Unit := do\n" + "".join(f"  harnessPart{g}\n" for g in range(len(groups))) + "  pure ()\n")

def _records(run: LeanRun, n: int) -> list[list[str]] | None:
  # the `@@a|b|...` lines a harness printed, one per input, or None when the run did not complete
  records = [line[2:].split("|") for line in run.output.splitlines() if line.startswith("@@")]
  return records if run.ok and len(records) == n else None

def run_rule(types_src: str, rule_src: str, schema: Schema, inputs: Sequence[dict[str, Any]], workdir: Path, **lean: Any) -> RuleRun:
  # run `f` (and `fallback`, if defined) over typed inputs. NOTE: expected outputs never enter the Lean source.
  values, with_fallback = schema["output"]["values"], has_fallback(rule_src)
  arms = "\n".join(f"  | .{v} => \"{v}\"" for v in values)
  flag = ' ++ "|" ++ (if fallback x then "1" else "0")' if with_fallback else ""
  source = f"{types_src}\n{rule_src}\n\ndef harnessSer : Output → String\n{arms}\n\n" + _harness(schema, inputs, f"harnessSer (f x){flag}")
  run = run_lean(source, workdir, run=True, timeout=RUN_TIMEOUT, **lean)
  if run.failure in ("timeout", "output_limit"): return RuleRun(None, "replay_timeout" if run.failure == "timeout" else "output_limit")
  records = _records(run, len(inputs))
  if records is None or any(r[0] not in values for r in records): return RuleRun(None, "replay_failed")
  outputs = [r[0] for r in records]
  if not with_fallback: return RuleRun(outputs)
  if any(len(r) != 2 or r[1] not in ("0", "1") for r in records): return RuleRun(None, "replay_failed")
  return RuleRun(outputs, fallback=[r[1] == "1" for r in records])

def derived_defs(expressions: Sequence[str], keep: Sequence[int] | None = None, notes: Sequence[str] = ()) -> str:
  # Lean helpers for derived quantities: `def derived<k> (x : Input) : Nat := <expression>`, each under its note as a comment
  return "".join((f"-- {notes[k]}\n" if notes else "") + f"def {derived_name(k)} (x : Input) : Nat :=\n  {expr}\n\n"
                 for k, expr in enumerate(expressions) if keep is None or k in keep)

def run_derived(types_src: str, expressions: Sequence[str], schema: Schema, inputs: Sequence[dict[str, Any]], workdir: Path, **lean: Any):
  # NOTE: Nat-valued expressions of `x : Input` are evaluated by Lean itself, so the numbers a fit sees are the numbers the rule will
  # compute. Returns one list of values per input, or the reason it failed (a str).
  defs = derived_defs(expressions)
  violations = scan_rule(defs)
  if violations: return ", ".join(violations)
  check = run_lean(f"{types_src}\n{defs}", workdir, run=False, timeout=CHECK_TIMEOUT, **lean)
  if not check.ok:
    return check.failure if check.failure != "crash" else "\n".join(line for line in check.output.splitlines() if "error" in line)[:600]
  line = ' ++ "|" ++ '.join(f"toString ({derived_name(k)} x)" for k in range(len(expressions)))
  run = run_lean(f"{types_src}\n{defs}" + _harness(schema, inputs, line), workdir, run=True, timeout=RUN_TIMEOUT, **lean)
  records = _records(run, len(inputs))
  if records is None or any(len(r) != len(expressions) or not all(v.isdigit() for v in r) for r in records):
    return run.failure or "the expressions could not be evaluated"
  return [[int(v) for v in r] for r in records]
