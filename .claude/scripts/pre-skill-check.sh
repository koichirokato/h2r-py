#!/usr/bin/env bash
# Pre-skill check: run automatically before Skill tool invocations via Claude Code
# hook. Blocks code-writing / shipping skills (implement, test-review, ship-pr) when
# invoked from the main worktree checkout instead of a linked worktree. scope-work is
# read-only and is intentionally allowed to run from the main checkout.
set -euo pipefail

BLOCKED_SKILLS=(implement test-review ship-pr)

input="$(cat)"
skill="$(jq -r '.tool_input.skill // empty' <<<"$input")"
session_cwd="$(jq -r '.cwd // empty' <<<"$input")"

# No opinion: exit 0 with no stdout so normal permission handling applies. Emitting
# an explicit "allow" decision here would affirmatively approve every Skill
# invocation in the repo (including unrelated/plugin skills), which is broader than
# this hook's job of blocking three specific skills from the main checkout.
allow() {
  exit 0
}

is_blocked=false
for name in "${BLOCKED_SKILLS[@]}"; do
  if [[ "$skill" == "$name" ]]; then
    is_blocked=true
    break
  fi
done

if [[ "$is_blocked" != "true" ]]; then
  allow
fi

# Prefer the session's reported cwd; fall back to the hook process's own cwd if it's
# missing for some reason. `--path-format=absolute` avoids git printing
# `--git-common-dir` relative to the toplevel instead of the query directory, which
# would otherwise make an unrelated subdirectory look like a false match/mismatch.
target_dir="${session_cwd:-$PWD}"

if ! git -C "$target_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  # Not inside a git repo at all: let the tool call proceed rather than block on an
  # unrelated failure mode.
  allow
fi

git_dir="$(git -C "$target_dir" rev-parse --path-format=absolute --git-dir)"
git_common_dir="$(git -C "$target_dir" rev-parse --path-format=absolute --git-common-dir)"

if [[ "$git_dir" == "$git_common_dir" ]]; then
  message="Run \`wt switch --create <branch>\` first — do not run implement/test-review/ship-pr from the main worktree checkout."
  jq -n \
    --arg message "$message" \
    '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": $message}, "systemMessage": $message}'
  exit 0
fi

allow
