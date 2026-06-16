#!/usr/bin/env bash
# Install Trusted-Loop Mode into a Claude Code project or globally.
#
# Usage:
#   ./setup.sh --project /path/to/your/project   # project-level install
#   ./setup.sh --global                           # user-level install (~/.claude)
#   ./setup.sh --uninstall --project <path>       # remove from a project
#   ./setup.sh --uninstall --global               # remove from user config

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPTS_DIR="$PLUGIN_ROOT/agents/claude-code/hooks_scripts"
CMD_SRC="$PLUGIN_ROOT/agents/claude-code/commands"

usage() {
  cat >&2 <<EOF
Usage:
  $0 --project <path>           Install into a specific project
  $0 --global                   Install into ~/.claude (all sessions)
  $0 --uninstall --project <path>
  $0 --uninstall --global
EOF
  exit 1
}

require_jq() {
  if ! command -v jq &>/dev/null; then
    echo "Error: jq is required. Install it (e.g. 'brew install jq' or 'apt install jq') and retry." >&2
    exit 1
  fi
}

MODE=""
TARGET_DIR=""
UNINSTALL=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ -z "${2:-}" ]] && { echo "Error: --project requires a path." >&2; usage; }
      MODE="project"; TARGET_DIR="$2"; shift 2 ;;
    --global)
      MODE="global"; TARGET_DIR="$HOME/.claude"; shift ;;
    --uninstall)
      UNINSTALL=true; shift ;;
    *)
      usage ;;
  esac
done

[[ -z "$MODE" ]] && usage

if [[ "$MODE" == "project" ]]; then
  [[ -d "$TARGET_DIR" ]] || { echo "Error: project directory '$TARGET_DIR' does not exist." >&2; exit 1; }
  CLAUDE_DIR="$TARGET_DIR/.claude"
else
  CLAUDE_DIR="$TARGET_DIR"
fi

SETTINGS="$CLAUDE_DIR/settings.json"
CMD_DST="$CLAUDE_DIR/commands"
COMMANDS=(loop-set-goal.md loop-clear-goal.md loop-status.md loop-judges.md)

if $UNINSTALL; then
  echo "Removing Trusted-Loop commands ..."
  for f in "${COMMANDS[@]}"; do rm -f "$CMD_DST/$f"; done

  if [[ -f "$SETTINGS" ]] && command -v jq &>/dev/null; then
    echo "Removing Trusted-Loop hooks from $SETTINGS ..."
    TMP="$(mktemp)"
    # Match on the absolute hooks_scripts dir, not a name substring — robust to
    # the plugin path's casing and clone location.
    jq --arg marker "$SCRIPTS_DIR" '
      .hooks.Stop         = [.hooks.Stop[]?         | select(.hooks[]?.command | contains($marker) | not)] |
      .hooks.SubagentStop = [.hooks.SubagentStop[]? | select(.hooks[]?.command | contains($marker) | not)] |
      .hooks.PreCompact   = [.hooks.PreCompact[]?   | select(.hooks[]?.command | contains($marker) | not)] |
      .hooks.SessionStart = [.hooks.SessionStart[]? | select(.hooks[]?.command | contains($marker) | not)]
    ' "$SETTINGS" > "$TMP"
    mv "$TMP" "$SETTINGS"
  fi

  echo "Done. Trusted-Loop has been removed."
  exit 0
fi

require_jq
mkdir -p "$CMD_DST"

echo "Installing commands to $CMD_DST ..."
for f in "${COMMANDS[@]}"; do
  sed "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_ROOT|g" "$CMD_SRC/$f" > "$CMD_DST/$f"
done

echo "Merging hooks into $SETTINGS ..."
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"

# Clean-path assignments (LHS is a plain path; RHS is computed). This avoids the
# `(.path // []) |= ...` form, which jq 1.6 rejects as an invalid path expression.
# jq and mv are separate statements so `set -e` aborts on a jq failure instead of
# silently leaving settings unchanged.
TMP="$(mktemp)"
jq --arg marker "$SCRIPTS_DIR" \
   --arg stop "python3 $SCRIPTS_DIR/on_stop.py" \
   --arg sub  "python3 $SCRIPTS_DIR/on_stop.py --subagent" \
   --arg pre  "python3 $SCRIPTS_DIR/on_precompact.py" \
   --arg ss   "python3 $SCRIPTS_DIR/on_session_start.py" '
  .hooks = (.hooks // {})
  | .hooks.Stop         = (((.hooks.Stop         // []) | map(select(.hooks[]?.command | contains($marker) | not))) + [{"hooks": [{"type": "command", "command": $stop, "timeout": 300}]}])
  | .hooks.SubagentStop = (((.hooks.SubagentStop // []) | map(select(.hooks[]?.command | contains($marker) | not))) + [{"hooks": [{"type": "command", "command": $sub,  "timeout": 300}]}])
  | .hooks.PreCompact   = (((.hooks.PreCompact   // []) | map(select(.hooks[]?.command | contains($marker) | not))) + [{"matcher": "auto|manual", "hooks": [{"type": "command", "command": $pre}]}])
  | .hooks.SessionStart = (((.hooks.SessionStart // []) | map(select(.hooks[]?.command | contains($marker) | not))) + [{"matcher": "compact|resume", "hooks": [{"type": "command", "command": $ss}]}])
' "$SETTINGS" > "$TMP"
mv "$TMP" "$SETTINGS"

echo ""
echo "Trusted-Loop Mode installed."
echo "  Commands : $CMD_DST/{loop-set-goal,loop-clear-goal,loop-status,loop-judges}.md"
echo "  Hooks    : $SETTINGS"
echo ""
echo "Next: configure at least one judge with /loop-judges, then /loop-set-goal."
