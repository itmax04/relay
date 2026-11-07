# Third-party skills

These skills are copied unchanged from their upstream repositories at the pinned commits below
(fetched with `gh api` on 2026-11-07). Each directory keeps the upstream `LICENSE` (all MIT).
`relay install` links them like relay's own skills; do not edit the copies, update them by
fetching a newer commit and replacing the whole directory.

| Skill | Upstream path | Repository | Commit |
|---|---|---|---|
| `ponytail` | `skills/ponytail/` (only `SKILL.md`) | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | `96608e078b2d5ea607c5c3b8b5a54d1c1fba6846` |
| `diagnosing-bugs` | `skills/engineering/diagnosing-bugs/` | [mattpocock/skills](https://github.com/mattpocock/skills) | `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d` |
| `grilling` | `skills/productivity/grilling/` | [mattpocock/skills](https://github.com/mattpocock/skills) | `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d` |
| `tdd` | `skills/engineering/tdd/` | [mattpocock/skills](https://github.com/mattpocock/skills) | `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d` |
| `writing-for-agents` | `skills/productivity/writing-for-agents/` | [mattpocock/skills](https://github.com/mattpocock/skills) | `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d` |
| `verification-before-completion` | `skills/verification-before-completion/` | [obra/superpowers](https://github.com/obra/superpowers) | `22f0b463e7a5e579904dd6211a520321035526f7` |

## References to skills that are not in the pack
- `tdd` tells the agent to call the `codebase-design` skill (mattpocock/skills
  `skills/engineering/codebase-design`) and points to the `code-review` skill
  (`skills/engineering/code-review`). Neither is copied. In Claude Code the name `code-review`
  resolves to Claude Code's own built-in review skill, not to the upstream one.
- `ponytail` mentions `/ponytail lite|full|ultra`: these are levels of the same skill, not other
  skills. The sibling skills of the upstream repo (`ponytail-audit`, `-debt`, `-gain`, `-help`,
  `-review`) are not copied and the main skill does not reference them.
- `diagnosing-bugs`, `grilling`, `writing-for-agents` and `verification-before-completion` only
  reference files inside their own directory.
