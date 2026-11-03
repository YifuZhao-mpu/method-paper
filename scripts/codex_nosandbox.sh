#!/usr/bin/env bash
# codex without its own sandbox, for containers where Codex's bubblewrap sandbox cannot start
# ("bwrap: No permissions to create a new namespace"). The container is then the only sandbox.
#
# Meant for ARIS's codex MCP bridge, which runs `$CODEX_BIN exec ... -` and `$CODEX_BIN exec resume <id> ... -`:
#   claude mcp remove codex -s user
#   claude mcp add codex -s user -e CODEX_BIN=<this file> -- python3 <ARIS>/mcp-servers/codex-exec/server.py
#
# Every sandbox setting in the arguments (--sandbox X, -s X, --sandbox=X, -c sandbox_mode=X) becomes
# danger-full-access, and for `exec` one more `-c sandbox_mode="danger-full-access"` goes before the trailing "-"
# (prompt on stdin). `codex exec` never asks for approval, so this is what --dangerously-bypass-approvals-and-sandbox
# does; the config form is used because `codex exec resume` takes no --sandbox flag.
# The real codex is $CODEX_REAL, default `codex` on PATH.
real="${CODEX_REAL:-codex}"
full='sandbox_mode="danger-full-access"'
out=()
want=""
for a in "$@"; do
  if [ "$want" = sandbox ]; then out+=("danger-full-access"); want=""; continue; fi
  if [ "$want" = config ]; then
    case "$a" in sandbox_mode=*) out+=("$full");; *) out+=("$a");; esac
    want=""; continue
  fi
  case "$a" in
    --sandbox|-s) out+=("$a"); want=sandbox ;;
    --sandbox=*) out+=("--sandbox=danger-full-access") ;;
    -c|--config) out+=("$a"); want=config ;;
    --config=sandbox_mode=*) out+=("--config=$full") ;;
    *) out+=("$a") ;;
  esac
done
if [ "${out[0]:-}" = exec ]; then
  n=${#out[@]}
  if [ "${out[$((n - 1))]}" = "-" ]; then
    out=("${out[@]:0:$((n - 1))}" -c "$full" -)
  else
    out+=(-c "$full")
  fi
fi
exec "$real" "${out[@]}"
