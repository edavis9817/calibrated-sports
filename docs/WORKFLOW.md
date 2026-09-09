# How the two tools divide

Two Claudes, two jobs. The failure mode to avoid is planning in one and
re-explaining in the other — that gap is where design drift enters and it is
the reason projects need a v2 in three weeks.

**The rule: no decision exists until it's in the repo.** If it was agreed in
chat and didn't land in `CLAUDE.md`, `docs/SPEC.md`, `DECISIONS.md` or a brief,
it will be violated within two weeks. Not from carelessness — the terminal
simply can't see it.

## Chat (this one) — the design and research engine

Good at, and should own:

- Research against public data. It has network access to nflverse and GitHub
  and can run real analysis — every number in `research/` came from here.
- Design arguments, pushback, and settling tradeoffs before code exists.
- Writing **briefs** (below), specs, and self-contained modules that can be
  written and tested without touching your machine.
- Reviewing output: paste a diff or a failing test and argue about it.

Cannot do: reach Kalshi, Polymarket or the Odds API (blocked by egress policy),
touch your filesystem, run git, or deploy anything.

## Claude Code — the build engine

Owns everything that touches the repo or the real world:

- Multi-file implementation and refactors against the actual codebase
- Running the code against **live** endpoints — the ones chat can't reach
- git, CI, systemd, Render, deployment
- Iterating on failing tests with real data in front of it

It reads `CLAUDE.md` automatically every session, which is what keeps the
invariants asserted without you restating them.

## The handoff artifact: a brief

Briefs live in `docs/briefs/NNN-name.md`, are written in chat, committed, and
executed by Claude Code. A conversation does not survive; a brief does.

Every brief has five sections, and the last two are what make it work:

```
## Goal            one paragraph, what done looks like
## Contract        the interface it must expose or consume
## Invariants      which CLAUDE.md rules this touches
## Acceptance      tests that must pass — specific, checkable
## Out of scope    what NOT to build (prevents scope creep in the agent)
```

Then in the terminal:

    Read CLAUDE.md and docs/briefs/002-nflverse-ingest.md, then implement it.
    Push back if any of it looks wrong before you start.

## DECISIONS.md

Append-only. One line per settled decision, dated, with the reason. When a
future session asks "why is storage SQLite," the answer is in the repo rather
than in a chat you can't find. Add an entry the moment something is settled —
naming, hosting, a modelling choice, a rejected approach.
