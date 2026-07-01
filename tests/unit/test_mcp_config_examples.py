import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_CONFIGS = [
    REPO_ROOT / ".mcp.json.example",
    REPO_ROOT / ".cursor" / "mcp.json",
    REPO_ROOT / ".vscode" / "mcp.json.example",
]
COMMITTED_CONFIGS = [
    *CLIENT_CONFIGS,
    REPO_ROOT / ".cursor" / "mcp.dev.json",
    REPO_ROOT / ".claude" / "launch.json",
]
LOCAL_ONLY_CONFIGS = [
    ".mcp.json",
    ".claude/mcp.json",
    ".claude/settings.local.json",
    ".vscode/mcp.json",
]


def _router_env(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    servers = data.get("mcpServers") or data.get("servers", {})
    router = servers.get("centralmcp") or servers.get("aruba-tool-router")
    assert router is not None, f"{path.relative_to(REPO_ROOT)} must define the centralmcp router"
    return router.get("env", {})


def test_committed_mcp_client_configs_use_low_token_router_profile():
    for path in CLIENT_CONFIGS:
        env = _router_env(path)

        assert env.get("CENTRALMCP_ROUTER_MODE") == "minimal"
        assert env.get("CENTRALMCP_TOOLSETS") == "central,glp,rag"
        assert "CENTRALMCP_PRODUCTS" not in env


def test_committed_mcp_configs_do_not_include_local_filesystem_servers():
    for path in COMMITTED_CONFIGS:
        text = path.read_text()
        data = json.loads(text)
        servers = data.get("mcpServers") or data.get("servers", {})

        assert "obsidian-vault" not in servers
        assert "/Users/" not in text


def test_local_only_mcp_configs_are_not_tracked():
    tracked = subprocess.check_output(
        ["git", "ls-files", *LOCAL_ONLY_CONFIGS],
        cwd=REPO_ROOT,
        text=True,
    ).splitlines()

    assert tracked == []


def test_claude_launch_includes_low_token_router_profile():
    data = json.loads((REPO_ROOT / ".claude" / "launch.json").read_text())
    configs = data.get("configurations", [])
    router = next(
        (
            config
            for config in configs
            if config.get("name") == "aruba-tool-router MCP server (minimal)"
        ),
        None,
    )

    assert router is not None
    assert router.get("runtimeArgs") == ["-m", "mcp_servers.tool_router"]
    assert router.get("env", {}).get("CENTRALMCP_ROUTER_MODE") == "minimal"
    assert router.get("env", {}).get("CENTRALMCP_TOOLSETS") == "central,glp,rag"
    assert "CENTRALMCP_PRODUCTS" not in router.get("env", {})


def test_repo_agent_docs_reference_claude_launch_router_config():
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text()
    mcp_engineer = (REPO_ROOT / ".claude" / "agents" / "mcp-engineer.md").read_text()

    for text in (claude_md, mcp_engineer):
        assert ".claude/launch.json" in text
        assert "CENTRALMCP_TOOLSETS=central,glp,rag" in text


def test_public_setup_docs_reference_claude_launch_router_config():
    readme = (REPO_ROOT / "README.md").read_text()
    getting_started = (REPO_ROOT / "docs" / "getting-started.md").read_text()

    for text in (readme, getting_started):
        assert ".claude/launch.json" in text
        assert "minimal" in text
        assert "aruba-tool-router" in text
