import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_CONFIGS = [
    REPO_ROOT / ".mcp.json.example",
    REPO_ROOT / ".cursor" / "mcp.json",
]


def _router_env(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    servers = data.get("mcpServers", {})
    router = servers.get("centralmcp") or servers.get("aruba-tool-router")
    assert router is not None, f"{path.relative_to(REPO_ROOT)} must define the centralmcp router"
    return router.get("env", {})


def test_committed_mcp_client_configs_use_low_token_router_profile():
    for path in CLIENT_CONFIGS:
        env = _router_env(path)

        assert env.get("CENTRALMCP_ROUTER_MODE") == "minimal"
        assert env.get("CENTRALMCP_TOOLSETS") == "central,glp,rag"
        assert "CENTRALMCP_PRODUCTS" not in env
