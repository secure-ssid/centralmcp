#!/usr/bin/env bash
set -euo pipefail

export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8010}"
export CENTRALMCP_ROUTER_MODE="${CENTRALMCP_ROUTER_MODE:-minimal}"
export CENTRALMCP_TOOLSETS="${CENTRALMCP_TOOLSETS:-central,glp,rag}"

port_is_listening() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${MCP_PORT}" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - "${MCP_HOST}" "${MCP_PORT}" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
target = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.25)
    sys.exit(0 if sock.connect_ex((target, port)) == 0 else 1)
PY
    return
  fi

  return 1
}

if port_is_listening; then
  {
    echo "Port ${MCP_PORT} is already in use; not starting another router."
    if command -v lsof >/dev/null 2>&1; then
      lsof -nP -iTCP:"${MCP_PORT}" -sTCP:LISTEN
    else
      echo "A TCP listener accepted connections on ${MCP_HOST}:${MCP_PORT}."
    fi
    echo
    echo "Stop the existing listener with: kill <PID>"
  } >&2
  exit 1
fi

cat <<EOF
Starting centralmcp HTTP router
  endpoint: http://${MCP_HOST}:${MCP_PORT}/mcp
  mode:     ${CENTRALMCP_ROUTER_MODE}
  toolsets: ${CENTRALMCP_TOOLSETS}

Foreground stop: Ctrl-C
Background stop:
  lsof -nP -iTCP:${MCP_PORT} -sTCP:LISTEN
  kill <PID>
EOF

exec uv run python -m mcp_servers.tool_router
