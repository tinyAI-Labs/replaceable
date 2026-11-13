#!/usr/bin/env python3
# NOTE: the line budget keeps the skill small enough to read in one sitting; raising MAX_LINE_COUNT is a decision, not a fix
import sys, token, tokenize
from pathlib import Path

MAX_LINE_COUNT, TOKENS = 1400, {token.OP, token.NAME, token.NUMBER, token.STRING}

def lines(path):
  tokens = tokenize.generate_tokens(open(path, encoding="utf-8").readline)
  return len({n for t in tokens if t.type in TOKENS for n in range(t.start[0], t.end[0] + 1)})

if __name__ == "__main__":
  root = Path(__file__).parent
  table = sorted((lines(p), str(p.relative_to(root))) for p in root.glob("skills/*/scripts/*.py"))
  for n, name in table: print(f"{n:6d}  {name}")
  total = sum(n for n, _ in table)
  print(f"{total:6d}  total (budget {MAX_LINE_COUNT})")
  sys.exit(f"over the line budget: {total} > {MAX_LINE_COUNT}" if total > MAX_LINE_COUNT else 0)
