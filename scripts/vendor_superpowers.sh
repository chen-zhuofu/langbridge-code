#!/usr/bin/env bash
# Vendor obra/superpowers skills into src/langbridge_code/skills/superpowers/
# Source: https://github.com/obra/superpowers (MIT)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/src/langbridge_code/skills/superpowers"
BASE="https://raw.githubusercontent.com/obra/superpowers/main/skills"
SKILLS=(
  brainstorming dispatching-parallel-agents executing-plans finishing-a-development-branch
  receiving-code-review requesting-code-review subagent-driven-development systematic-debugging
  test-driven-development using-git-worktrees using-superpowers verification-before-completion
  writing-plans writing-skills
)
mkdir -p "$DEST"
for s in "${SKILLS[@]}"; do
  mkdir -p "$DEST/$s"
  curl -fsSL "$BASE/$s/SKILL.md" -o "$DEST/$s/SKILL.md"
  echo "  $s"
done
echo "Vendored ${#SKILLS[@]} superpowers skills into $DEST"
