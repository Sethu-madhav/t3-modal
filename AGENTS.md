# t3modal

t3modal rebuilds t3boat on Modal Sandboxes. The design is in `implementation.md`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `Sethu-madhav/t3-modal`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Commit attribution

GitHub's Contributors list counts only commit authors and ignores `Co-Authored-By` lines. So each agent makes its commits with itself as the author:

- Claude: `git commit --author="Claude <noreply@anthropic.com>"`. GitHub links this email to the `claude` account.
- Antigravity: `git commit --author="gemini-code-assist[bot] <176961590+gemini-code-assist[bot]@users.noreply.github.com>"`. GitHub links it to Google's Gemini bot, since Antigravity has no GitHub account.

End the message with `Co-Authored-By: Sethu madhav <30382594+Sethu-madhav@users.noreply.github.com>`, so the commit also shows the user. When both agents wrote a commit, the one committing is the author and the other goes in a second `Co-Authored-By` line. Merge PRs with a merge commit, which keeps each agent's commits on `main` as they are. End each PR description with a line naming the agent that wrote it, such as `Generated with Antigravity (Gemini 4 Neon)`.
