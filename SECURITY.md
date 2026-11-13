# Security

- The scripts are standard-library Python and run `lean` on the rule they fitted (or one you wrote) inside your working directory. They make no network calls and read no credentials.
- A rule is scanned before Lean sees it (`scripts/lean.py`: no `import`, `IO`, `#eval`, `unsafe`, axioms ...), and Lean runs with time and output limits. This is a contract check, not a sandbox: do not run the skill on a `Rule.lean` or a log from a source you do not trust outside a container.
- The held-out guarantee depends on the host: the scripts refuse to fit on `logs/heldout.jsonl` and allow one replay, but cannot stop an agent that reads files. Keep that file out of the agent's reach and use `host_replay.py`.
- Logs can hold personal or confidential data. Everything stays in the working directory (`logs/`, `work/`, `verdict.json`); nothing is uploaded.
- Report a vulnerability privately through the repository's "Report a vulnerability" (GitHub security advisory) button, not in a public issue. Expect an answer within a week.
