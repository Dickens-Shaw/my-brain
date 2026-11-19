#!/usr/bin/env bash
#
# Install the repository's skills into the directories the agents read.
#
# The skill definitions live in `skills/` so they are version controlled —
# `.cursor/` is gitignored, so a skill written there would never reach
# another machine. This script links them into place instead of copying, so
# editing a skill in `skills/` takes effect immediately.
#
# Usage: bash scripts/install-skills.sh [--copy]
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_SRC="$REPO_ROOT/skills"
MODE="link"

if [[ "${1:-}" == "--copy" ]]; then
  MODE="copy"
fi

if [[ ! -d "$SKILLS_SRC" ]]; then
  echo "No skills/ directory at $SKILLS_SRC"
  exit 0
fi

# Where each agent looks for skills.
TARGETS=(
  "$REPO_ROOT/.cursor/skills"
  "$HOME/.claude/skills"
)

installed=0
for target in "${TARGETS[@]}"; do
  [[ -d "$SKILLS_SRC" ]] || continue
  mkdir -p "$target"

  for skill_dir in "$SKILLS_SRC"/*/; do
    [[ -d "$skill_dir" ]] || continue
    name="$(basename "$skill_dir")"
    [[ -f "$skill_dir/SKILL.md" ]] || continue

    dest="$target/$name"

    # A real directory (not a symlink) at the destination means someone put
    # their own version there. Leave it alone and say so.
    if [[ -e "$dest" && ! -L "$dest" ]]; then
      echo "  skip  $name — $dest exists and is not a symlink"
      continue
    fi

    rm -rf "$dest"
    if [[ "$MODE" == "copy" ]]; then
      cp -R "$skill_dir" "$dest"
      echo "  copy  $name → $dest"
    else
      ln -s "$skill_dir" "$dest"
      echo "  link  $name → $dest"
    fi
    installed=$((installed + 1))
  done
done

if [[ $installed -eq 0 ]]; then
  echo "Nothing installed."
else
  echo ""
  echo "Installed $installed skill(s). Restart the agent to pick them up."
fi
