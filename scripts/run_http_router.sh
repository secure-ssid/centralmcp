#!/usr/bin/env bash
set -euo pipefail

export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8010}"
export CENTRALMCP_ROUTER_MODE="${CENTRALMCP_ROUTER_MODE:-minimal}"
export CENTRALMCP_TOOLSETS="${CENTRALMCP_TOOLSETS:-central,glp,rag}"

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
