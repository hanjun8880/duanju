#!/usr/bin/env bash
# PostToolUse: lint/type-check the edited file. Claude Code passes tool input as JSON on stdin.
f=$(jq -r '.tool_input.file_path // empty')
root=$(git rev-parse --show-toplevel)
case "$f" in
  *apps/server/*.py) cd "$root/apps/server" && uv run ruff check --fix "$f" && uv run mypy src ;;
  *apps/web/*.ts|*apps/web/*.tsx) cd "$root/apps/web" && pnpm tsc --noEmit ;;
esac
