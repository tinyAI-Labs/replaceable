#!/usr/bin/env python3
# NOTE: the line budget keeps the skill small enough to read in one sitting; raising MAX_LINE_COUNT is a decision, not a fix
import sys, token, tokenize
from pathlib import Path

MAX_LINE_COUNT, TOKENS = 1402, {token.OP, token.NAME, token.NUMBER, token.STRING}

MAX_TINYAI_LINES = 110  # the optional tiny-model code in scripts/tinyai/ has a budget of its own: the core budget stays the size of the skill

def lines(path):
  tokens = tokenize.generate_tokens(open(path, encoding="utf-8").readline)
  return len({n for t in tokens if t.type in TOKENS for n in range(t.start[0], t.end[0] + 1)})

def over(root, pattern, budget):
  # print the code lines of the files matching `pattern` and their total; True when the total is over `budget`
  table = sorted((lines(p), str(p.relative_to(root))) for p in root.glob(pattern))
  for n, name in table: print(f"{n:6d}  {name}")
  total = sum(n for n, _ in table)
  print(f"{total:6d}  total (budget {budget})")
  return total > budget

if __name__ == "__main__":
  root = Path(__file__).parent
  core, tinyai = over(root, "skills/*/scripts/*.py", MAX_LINE_COUNT), over(root, "skills/*/scripts/tinyai/*.py", MAX_TINYAI_LINES)
  sys.exit("over the line budget" if core or tinyai else 0)
