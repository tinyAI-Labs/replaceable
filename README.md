# replaceable

Find out which agent loops can be replaced by a fixed rule.

Many agent loops are a fixed policy in disguise. `replaceable` is an agent skill that tests this from a loop's own log: it fits a rule on the early half, replays it once on the half it has never seen, and reports a verdict that a script computes. A weak result is never a wrong answer: when no rule is found, the verdict is inconclusive.

**Status:** v0.1 in progress. The skill is being added in small pull requests; this README is updated with install and usage when it lands.

License: Apache-2.0.
