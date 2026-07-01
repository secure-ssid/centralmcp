#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${ROOT}/.env" ]]; then
  while IFS= read -r assignment; do
    export "${assignment}"
  done < <(python3 - "${ROOT}/.env" <<'PY'
import re
import shlex
import sys

env_key = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
allowed_keys = {
    "CENTRALMCP_PRODUCTS",
    "CENTRALMCP_PRODUCT_ACCESS",
    "CENTRALMCP_ALLOW_LOCAL_PRODUCT_URLS",
    "CENTRALMCP_ROUTER_MODE",
    "CENTRALMCP_TOOLSETS",
    "CENTRALMCP_GLP_V2BETA1_WRITES",
    "MCP_HOST",
    "MCP_PORT",
    "CLEARPASS_BASE_URL",
    "CLEARPASS_API_TOKEN",
    "MIST_HOST",
    "MIST_API_TOKEN",
    "APSTRA_BASE_URL",
    "APSTRA_API_TOKEN",
    "AOS8_BASE_URL",
    "AOS8_API_TOKEN",
    "EDGECONNECT_BASE_URL",
    "EDGECONNECT_API_TOKEN",
    "EDGECONNECT_AUTH_HEADER",
    "UXI_CLIENT_ID",
    "UXI_CLIENT_SECRET",
    "UXI_BASE_URL",
    "UXI_TOKEN_URL",
}
for raw_line in open(sys.argv[1]):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    try:
        parts = shlex.split(line, comments=False, posix=True)
    except ValueError:
        continue
    if parts and parts[0] == "export":
        parts = parts[1:]
    if len(parts) != 1 or "=" not in parts[0]:
        continue
    key, value = parts[0].split("=", 1)
    if key in allowed_keys and env_key.match(key):
        print(f"{key}={value}")
PY
  )
fi

export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8010}"
export CENTRALMCP_ROUTER_MODE="${CENTRALMCP_ROUTER_MODE:-minimal}"
export CENTRALMCP_TOOLSETS="${CENTRALMCP_TOOLSETS:-central,glp,rag}"
export CENTRALMCP_PRODUCT_ACCESS="${CENTRALMCP_PRODUCT_ACCESS:-read-only}"

case "${MCP_HOST}" in
  127.0.0.1|localhost|::1) ;;
  *)
    {
      echo "WARNING: MCP_HOST=${MCP_HOST} is not loopback."
      echo "Credential-backed MCP tools may be reachable from the network; protect with firewall/auth/TLS."
      echo
    } >&2
    ;;
esac

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
for family, socktype, proto, _, sockaddr in socket.getaddrinfo(target, port, type=socket.SOCK_STREAM):
    with socket.socket(family, socktype, proto) as sock:
        sock.settimeout(0.25)
        if sock.connect_ex(sockaddr) == 0:
            sys.exit(0)
sys.exit(1)
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
  products: ${CENTRALMCP_PRODUCTS:-none}
  access:   ${CENTRALMCP_PRODUCT_ACCESS}

Foreground stop: Ctrl-C
Background stop:
  lsof -nP -iTCP:${MCP_PORT} -sTCP:LISTEN
  kill <PID>
EOF

cd "${ROOT}"
exec uv run python -m mcp_servers.tool_router
