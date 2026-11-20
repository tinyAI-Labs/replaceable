# the verdict as a page: a compact block for the terminal and the full picture as report.md (terminal, Claude app) and report.html (one file,
# no dependencies). Both are built from verdict.json's fields alone: nothing here decides anything.
from __future__ import annotations
import html
from pathlib import Path
from typing import Any
import verdict as v

BARS = "▁▂▃▄▅▆▇█"
NEXT = {"DETERMINISTIC": "A person confirms that Rule.lean matches the written policy before it replaces the loop.",
        "CONSTANT": "A person confirms that the loop really never varies before it is replaced by a constant.",
        "DETERMINISTIC_LEARNED": "The runs are reproducible but no short rule states them: a small trained model would replace the loop, not a rule.",
        "PROBABILISTIC": "The same inputs get different answers: no fixed rule or small model can replace this loop as it is.",
        "INCONCLUSIVE": "The rationale says why; more runs help only if the rule's agreement was still rising.",
        "OUT_OF_SCOPE": "The output is free text: this check does not apply."}

def floor1(share: float) -> str:
  return f"{int(share * 1000) / 10:.1f}%"  # rounded down, like v.pct: a figure below the bar must not read as the bar

def facts(result: dict[str, Any], usd: float | None = None) -> list[tuple[str, str, str]]:
  # (label, value, note) rows: what the loop did, what it costs and how the exam went
  t, learned, rows = result["tokens"], result.get("learning") or {}, []
  if learned.get("from_run"):
    trimmed = learned.get("trimmed")
    after = t.get("cases_with_tokens", 0) if trimmed and t["known"] else learned["runs"] - learned["from_run"]
    rows += [("Learning phase", f"{learned['from_run']:,} runs", f"{v.pct(learned['accuracy_before'])} right, set aside"),
             ("Converged loop", f"{after:,} runs", f"{v.pct(learned['accuracy'])} right, examined" + (", latest only" if trimmed else ""))]
  elif learned:
    state = "still moving" if learned.get("unsettled") else "steady, at or above 95%" if learned["converged"] else "steady, below 95%"
    rows.append(("Feedback", f"{v.pct(learned['accuracy'])} right", state))
  if t["known"]:
    rows.append(("Tokens per run", f"{t['per_run']:,.0f}", "converged loop" if learned.get("from_run") else "from the log"))
    if cost := v.learning_cost(t): rows.append(("Learning phase cost", f"{cost[1]:.1f}x per run", f"{cost[0]:,.0f} tokens per run"))
    month = t.get("per_month")
    total, saved, span = (month, round(t["rule_share"] * month), "a month") if month else (t["logged"], t["saved_logged"], "in the log")
    price = f"${total * usd / 1e6:,.2f} at ${usd:g} per million" if usd else "at the log's pace" if month else f"{t['cases_with_tokens']:,} runs"
    rows.append(("Tokens per month" if month else "Tokens in the log", f"{total:,}", price))
    if t["rule_share"]:
      bound = ", an upper bound" if t.get("note") else ""
      rows.append(("A rule would take over", f"{v.pct(t['rule_share'])} of runs", f"{saved:,} tokens {span}{bound}"))
  if model := result.get("model"):  # the learned check decided, not the rule
    held = f"{model['n']:,} cases, 99% lower bound {floor1(model['lower_bound'])}"
    rows += [("Learned model, held-back", f"{floor1(model['n_match'] / model['n'])} matched", held),
             ("Best constant guess", floor1(model["baseline"]), "what the model must beat")]
  elif "lower_bound" in result:
    held = f"{result['n_heldout']:,} cases, 95% lower bound {floor1(result['lower_bound'])}"
    rows += [("Held-back replay", f"{floor1(result['agreement'])} matched", held),
             ("Best constant guess", floor1(result["baseline"]), "what a rule must beat")]
  return rows

def next_step(result: dict[str, Any]) -> str:
  if result["verdict"] == "INCONCLUSIVE" and result.get("exit_stage") == "feedback":
    return "Run this again once the loop's feedback has been steady for 300 or more runs."
  return NEXT[result["verdict"]]

def spark(values: list[float | None], low: float, high: float) -> str:
  return "".join("·" if x is None else BARS[min(7, max(0, int((x - low) / ((high - low) or 1) * 8)))] for x in values)

def curves(result: dict[str, Any]) -> tuple[list[float], list[float | None]] | None:
  blocks = (result.get("learning") or {}).get("curve")
  return ([b[0] for b in blocks], [b[1] for b in blocks]) if blocks else None

def marker(result: dict[str, Any]) -> tuple[int, str] | None:
  # the run where the steady stretch begins, and what to call it
  learned = result.get("learning") or {}
  if learned.get("from_run"): return learned["from_run"], "held 95%"
  return (learned["examined_from"], "steady from here") if learned.get("examined_from") else None

def terminal(result: dict[str, Any], usd: float | None = None) -> str:
  # the compact answer: what the owner needs in one glance
  name, scope = result["verdict"], result.get("scope")
  return (f"replaceable · {name}" + (f" ({scope})" if scope else "") + f"\n{v.answer_line(result)}\n"
          f"{v.tokens_line(result['tokens'], usd, name in v.SCOPED)}\nFull report: report.html (browser) · report.md (terminal, Claude app)")

def markdown(result: dict[str, Any], usd: float | None = None) -> str:
  name, learned, out = result["verdict"], result.get("learning") or {}, []
  out += [f"# replaceable · {name}", "", f"> **{v.answer_line(result)[len('Answer: '):]}**", "", "| | | |", "|---|---:|---|"]
  out += [f"| {label} | **{value}** | {note} |" for label, value, note in facts(result, usd)]
  if (lines := curves(result)) and (n := learned.get("runs")):
    accuracy, tokens = lines
    mark, said = marker(result) or (None, "")
    out += ["", f"## The loop over time ({n:,} runs, {len(accuracy)} blocks)", "", "```",
            f"right per block  {spark(accuracy, 0.5, 1.0)}  {v.pct(accuracy[0])} → {v.pct(accuracy[-1])}"]
    if any(x is not None for x in tokens):
      known = [x for x in tokens if x is not None]
      out.append(f"tokens per run  {spark(tokens, 0, max(known))}  {known[0]:,.0f} → {known[-1]:,.0f}")
    out += ["```"] + ([f"The loop {said} from run {mark:,}."] if mark else [])
  out += ["", "## How it was decided", "", result["rationale"], "", "## Next", "", next_step(result)]
  if name in v.SCOPED: out += ["", f"*{v.ASK}*"]
  return "\n".join(out) + "\n"

CSS = Path(__file__).with_name("report.css").read_text(encoding="utf-8")

W, H, LEFT = 720, 150, 44  # the drawing, one panel's height, the room for the axis labels

def chart(result: dict[str, Any]) -> str:
  lines, learned = curves(result), result.get("learning") or {}
  if not lines or not learned.get("runs"): return ""
  accuracy, tokens = lines
  runs, mark, span = learned["runs"], marker(result), W - LEFT - 8
  x = lambda i: LEFT + span * (i + 0.5) / len(accuracy)  # noqa: E731
  svg = [f'<svg viewBox="0 0 {W} {2 * H + 30}" role="img" aria-label="Accuracy and tokens per run over the log">']
  def panel(top: int, values: list[float | None], high: float, low: float, color: str, title: str, fmt: str) -> None:
    y = lambda value: top + 18 + (H - 36) * (1 - (max(low, min(high, value)) - low) / ((high - low) or 1))  # noqa: E731
    svg.append(f'<text class="lbl" x="{LEFT}" y="{top + 10}">{title}</text>')
    for level in (low, (low + high) / 2, high):
      svg.append(f'<line class="axis" x1="{LEFT}" x2="{W - 8}" y1="{y(level):.1f}" y2="{y(level):.1f}"/>'
                 f'<text class="lbl" x="{LEFT - 6}" y="{y(level) + 4:.1f}" text-anchor="end">{fmt.format(level)}</text>')
    if mark:
      edge = LEFT + span * mark[0] / runs
      svg.append(f'<rect x="{LEFT}" y="{top + 14}" width="{edge - LEFT:.1f}" height="{H - 28}" fill="{color}" opacity=".08"/>'
                 f'<line x1="{edge:.1f}" x2="{edge:.1f}" y1="{top + 14}" y2="{top + H - 14}" stroke="{color}" stroke-dasharray="4 3"/>')
    points = " ".join(f"{x(i):.1f},{y(val):.1f}" for i, val in enumerate(values) if val is not None)
    if points: svg.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" points="{points}"/>')
  panel(0, accuracy, 1.0, 0.5, "var(--acc)", "Share of runs right, per block of the log", "{:.0%}")
  known = [t for t in tokens if t is not None]
  if known: panel(H + 10, tokens, max(known), 0, "var(--tok)", "Tokens per run, per block of the log", "{:,.0f}")
  svg.append(f'<text class="lbl" x="{LEFT}" y="{2 * H + 26}">run 0</text>'
             f'<text class="lbl" x="{W - 8}" y="{2 * H + 26}" text-anchor="end">run {runs:,}</text>')
  if mark:
    svg.append(f'<text class="lbl" x="{LEFT + span * mark[0] / runs + 4:.1f}" y="{H - 4}">{html.escape(mark[1])} (run {mark[0]:,})</text>')
  return f'<div class="card"><h2>The loop over time</h2>{"".join(svg)}</svg></div>'

def page(result: dict[str, Any], usd: float | None = None) -> str:
  name, esc = result["verdict"], html.escape
  look = "good" if name in v.SCOPED else {"INCONCLUSIVE": "warn", "PROBABILISTIC": "bad"}.get(name, "none")
  cards = "".join(f'<div class="kpi"><span>{esc(a)}</span><b>{esc(b)}</b><i>{esc(c)}</i></div>' for a, b, c in facts(result, usd))
  ask = f'<p class="small">{esc(v.ASK)}</p>' if name in v.SCOPED else ""
  head = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
          f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Replaceable report</title><style>{CSS}</style></head>')
  return (f'{head}<body><main><span class="chip {look}">{esc(name)}</span><h1>{esc(v.answer_line(result)[len("Answer: "):])}</h1>'
          f'<div class="grid">{cards}</div>{chart(result)}'
          f'<div class="card"><h2>How it was decided</h2><p>{esc(result["rationale"])}</p></div>'
          f'<div class="card"><h2>Next</h2><p>{esc(next_step(result))}</p>{ask}</div></main></body></html>')

def write(result: dict[str, Any], usd: float | None = None) -> None:
  Path("report.md").write_text(markdown(result, usd), encoding="utf-8")
  Path("report.html").write_text(page(result, usd), encoding="utf-8")
