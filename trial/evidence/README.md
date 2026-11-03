# Trial evidence

What `docs/trial-results.md` and the comparison page rely on, extracted from both arms' Claude Code session logs on 2026-11-03.

- `summary.json`, per arm: the token counts (by main session, subagents and workflow agents), the time window, and the times of the owner's messages. Plus every report from arm B's 8 subagents, and each step result of arm C's 4 workflow runs, including the verifiers' "unexpected user" findings that the script dropped.
- `summarise.py` rebuilds `summary.json` from `~/.claude/projects`. The windows run from each queue's start to the end of that arm's final report: B 08:52:41 to 09:21:21, C 08:52:57 to 09:59:42 UTC. Later use of those sessions is left out.

The raw session logs aren't copied here, because they hold whole conversations. They stay in `~/.claude/projects/-var-home-Deryk-development-TuringCollege-sprint3-trail-{b,c}/`, and Claude Code may delete them after its retention period. Once they're gone, `summary.json` is the record, and the script can't rebuild it.

Checked: the token totals in `summary.json` match the page (B 14,622,092, C 13,582,855), and it holds no API keys.
