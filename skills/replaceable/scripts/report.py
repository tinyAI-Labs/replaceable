# the verdict for the person who decides: what the loop does, what it costs and whether a fixed rule could run it. A compact block for the terminal,
# the full picture as report.md (terminal, Claude app) and report.html (one file, no dependencies). Built from verdict.json's fields alone.
from __future__ import annotations
import html
from pathlib import Path
from typing import Any
import verdict as v

BARS = "▁▂▃▄▅▆▇█"
PLAIN = {  # (headline, what it means)
  "DETERMINISTIC": ("Predictable: a fixed rule can run this loop",
                    "A simple rule reproduced this loop's answers on runs it had never seen, so it can do this job without an LLM."),
  "CONSTANT": ("Always the same answer", "Every logged run ended the same way, so no LLM is needed."),
  "DETERMINISTIC_LEARNED": ("Predictable, but too intricate for a short rule",
                            "The same input gets the same answer, but no short readable rule describes it."),
  "PROBABILISTIC": ("Not predictable: this loop needs the LLM", "The same input gets different answers, so a fixed rule cannot replace it."),
  "INCONCLUSIVE": ("Cannot tell yet", "The log does not show clearly whether this loop is predictable."),
  "OUT_OF_SCOPE": ("Not applicable", "The loop writes free text, which this check cannot judge.")}
NEXT = {"DETERMINISTIC": "A person confirms that Rule.lean matches the written policy before it replaces the loop.",
        "CONSTANT": "A person confirms that the loop really never varies before it is replaced by a constant.",
        "DETERMINISTIC_LEARNED": "The runs are reproducible but no short rule states them: a small trained model would replace the loop, not a rule.",
        "PROBABILISTIC": "The same inputs get different answers: no fixed rule or small model can replace this loop as it is.",
        "INCONCLUSIVE": "More runs help only if the rule's agreement was still rising; the evidence below says whether it was.",
        "OUT_OF_SCOPE": "The output is free text: this check does not apply."}

def plain(result: dict[str, Any]) -> tuple[str, str]:
  # (headline, meaning); a decision the loop makes on text it first has to read is only partly replaceable
  if result.get("scope") == "decision_step_only" and result["verdict"] in v.SCOPED:
    return ("Predictable once the input is read: the decision can be a fixed rule",
            "Reading the input (documents, messages) still needs the LLM; the decision made on it can be a simple rule.")
  return PLAIN[result["verdict"]]

def floor1(share: float) -> str:
  return f"{int(share * 1000) / 10:.1f}%"  # rounded down, like v.pct: a figure below the bar must not read as the bar

def few(names: list[str], limit: int = 5) -> str:
  shown = names[:limit]
  if len(names) > limit: return ", ".join(shown) + f" and {len(names) - limit} more"
  return ", ".join(shown[:-1]) + (" and " if len(shown) > 1 else "") + "".join(shown[-1:])

def loop_text(result: dict[str, Any]) -> str | None:
  # the loop in one sentence: what it reads and what it can answer
  loop = result.get("loop")
  if not loop: return None
  answers = loop["answers"]
  if not answers: verb = "writes free text"
  elif len(answers) == 1: verb = f"always answers {answers[0]}"
  else: verb = f"decides one of {len(answers)} outcomes ({few(answers)})"
  return (f"Reads {few(loop['reads'])} and " if loop["reads"] else "") + verb + "."

def rule_lines(result: dict[str, Any]) -> list[str]:
  # what the loop does most, as "share of runs: when -> answer" (only for a loop a rule describes)
  lines = [f"{round(r['share'] * 100)}% of runs: " + (f"{r['when']} → " if r["when"] != "everywhere" else "always ") + r["then"]
           for r in result.get("rules", [])]
  count, share = result.get("rules_rest", [0, 0])
  return lines + ([f"{round(share * 100)}% of runs: {count} smaller cases"] if count else [])

def cost_rows(result: dict[str, Any], usd: float | None = None) -> list[tuple[str, str, str]]:
  # (label, value, note): what the loop costs per run, at scale, and while it was learning
  t, learned = result["tokens"], result.get("learning") or {}
  if not t["known"]: return []
  rows = [("Per run", f"{t['per_run']:,.0f} tokens", "once it works" if learned.get("from_run") else "from the log")]
  month = t.get("per_month")
  total = month or t["logged"]
  price = f"about ${total * usd / 1e6:,.2f} at ${usd:g} per million" if usd else "at the log's pace" if month else f"{t['cases_with_tokens']:,} runs"
  rows.append(("Per month" if month else "In the log", f"{total:,} tokens", price))
  if cost := v.learning_cost(t):
    spent = cost[0] * learned["from_run"]
    first = f"{spent:,.0f} tokens in all over the first {learned['from_run']:,} runs ({v.pct(learned['accuracy_before'])} right)"
    rows.append(("While it was learning", f"{cost[1]:.1f}x as much per run", first + (f", about ${spent * usd / 1e6:,.2f}" if usd else "")))
  return rows

def saving(result: dict[str, Any]) -> str | None:
  # what a fixed rule would cost instead
  t = result["tokens"]
  if not t["known"] or not t["rule_share"]: return None
  month = t.get("per_month")
  saved = f"{round(t['rule_share'] * month):,} tokens a month" if month else f"{t['saved_logged']:,} tokens over the log"
  if t.get("note"): return f"A fixed rule would run the decision for 0 tokens: at most {saved} saved, because reading the input still needs the LLM."
  return f"A fixed rule runs this for 0 tokens: about {saved} saved."

def evidence_rows(result: dict[str, Any]) -> list[tuple[str, str, str]]:
  # the numbers behind the verdict, for the technical reader
  learned, rows = result.get("learning") or {}, []
  if learned.get("from_run"):
    trimmed, t = learned.get("trimmed"), result["tokens"]
    after = t.get("cases_with_tokens", 0) if trimmed and t["known"] else learned["runs"] - learned["from_run"]
    rows += [("Learning phase", f"{learned['from_run']:,} runs", f"{v.pct(learned['accuracy_before'])} right, set aside"),
             ("Converged loop", f"{after:,} runs", f"{v.pct(learned['accuracy'])} right, examined" + (", latest only" if trimmed else ""))]
  elif learned:
    state = "still moving" if learned.get("unsettled") else "steady, at or above 95%" if learned["converged"] else "steady, below 95%"
    rows.append(("Feedback", f"{v.pct(learned['accuracy'])} right", state))
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
  # the compact answer: the loop, the verdict, the cost, and for a loop that could be replaced the question
  name, loop = result["verdict"], loop_text(result)
  title = f"replaceable · {name}" + (" (decision step only)" if result.get("scope") == "decision_step_only" else "")
  lines = [title, *([f"Loop: {loop}"] if loop else []), v.answer_line(result), v.tokens_line(result["tokens"], usd)]
  return "\n".join(lines + ([v.ASK] if name in v.SCOPED else []) + ["Full report: report.html (browser) · report.md (terminal, Claude app)"])

def markdown(result: dict[str, Any], usd: float | None = None) -> str:
  name, learned = result["verdict"], result.get("learning") or {}
  head, meaning = plain(result)
  out = [f"# {head}", "", f"> {meaning}", "", f"*{v.answer_line(result)[len('Answer: '):]}*", ""]
  if loop := loop_text(result):
    common = ["Its most common decisions:", ""] + [f"- {line}" for line in rule_lines(result)] + [""] if result.get("rules") else []
    out += ["## The loop", "", loop, ""] + common
  if rows := cost_rows(result, usd):
    out += ["## What it costs", "", "| | | |", "|---|---:|---|"] + [f"| {a} | **{b}** | {c} |" for a, b, c in rows] + [""]
  if (cheaper := saving(result)) or name in v.SCOPED:
    out += ["## What a fixed rule would cost" if cheaper else "## Cutting the spend", "", *([cheaper, ""] if cheaper else [])]
    out += [f"**{v.ASK}**", ""] if name in v.SCOPED else [""]
  if (lines := curves(result)) and (n := learned.get("runs")):
    accuracy, tokens = lines
    mark, said = marker(result) or (None, "")
    out += [f"## The loop over time ({n:,} runs)", "", "```"]
    out.append(f"right per block  {spark(accuracy, 0.5, 1.0)}  {v.pct(accuracy[0])} → {v.pct(accuracy[-1])}")
    if known := [x for x in tokens if x is not None]:
      out.append(f"tokens per run  {spark(tokens, 0, max(known))}  {known[0]:,.0f} → {known[-1]:,.0f}")
    out += ["```"] + ([f"The loop {said} from run {mark:,}."] if mark else []) + [""]
  out += ["## The evidence", ""] + [f"- {a}: **{b}** ({c})" for a, b, c in evidence_rows(result)]
  out += ["", result["rationale"], "", f"Next: {next_step(result)}"]
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
  head, meaning = plain(result)
  look = "good" if name in v.SCOPED else {"INCONCLUSIVE": "warn", "PROBABILISTIC": "bad"}.get(name, "none")
  card = lambda title, body: f'<div class="card"><h2>{title}</h2>{body}</div>' if body else ""  # noqa: E731
  cheaper = saving(result)
  loop = loop_text(result)
  rules = "".join(f"<li>{esc(line)}</li>" for line in rule_lines(result))
  costs = "".join(f'<div class="kpi"><span>{esc(a)}</span><b>{esc(b)}</b><i>{esc(c)}</i></div>' for a, b, c in cost_rows(result, usd))
  ask = f'<p class="cta"><a href="https://tinyailabs.com/">{esc(v.ASK)}</a></p>' if name in v.SCOPED else ""
  evidence = "".join(f"<tr><td>{esc(a)}</td><td><b>{esc(b)}</b></td><td>{esc(c)}</td></tr>" for a, b, c in evidence_rows(result))
  head_html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
               f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>Replaceable report</title><style>{CSS}</style></head>')
  return (f'{head_html}<body><main><span class="chip {look}">{esc(name)}</span><h1>{esc(head)}</h1><p>{esc(meaning)}</p>'
          f'<p class="small">{esc(v.answer_line(result)[len("Answer: "):])}</p>'
          + card("The loop", f"<p>{esc(loop)}</p>" + (f"<p>Its most common decisions:</p><ul>{rules}</ul>" if rules else "") if loop else "")
          + card("What it costs", f'<div class="grid">{costs}</div>' if costs else "")
          + card("What a fixed rule would cost" if cheaper else "Cutting the spend", f"<p>{esc(cheaper)}</p>{ask}" if cheaper else ask)
          + chart(result)
          + f'<details class="card"><summary>The evidence</summary><table>{evidence}</table><p>{esc(result["rationale"])}</p>'
          f"<p>Next: {esc(next_step(result))}</p></details></main></body></html>")

def write(result: dict[str, Any], usd: float | None = None) -> None:
  Path("report.md").write_text(markdown(result, usd), encoding="utf-8")
  Path("report.html").write_text(page(result, usd), encoding="utf-8")
