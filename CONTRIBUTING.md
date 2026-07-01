# Contributing

Thanks for helping improve centralmcp. This project is optimized for low-token,
lab-friendly HPE Networking MCP workflows, so changes should keep setup simple,
tool discovery compact, and credentials local.

Please follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) when participating in
issues, pull requests, reviews, and docs.

## Start locally

```bash
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
python3 scripts/setup_wizard.py --yes --skip-credentials
uv run python scripts/doctor.py
```

Use fake hosts, fake IDs, and redacted payloads in examples, tests, screenshots,
and issue comments. Do not commit real `config/credentials.yaml`, `.env`, token
caches, tenant IDs, customer data, generated indexes, or local MCP config files.

## Before opening a pull request

1. Keep changes focused and update the matching docs when behavior, setup,
   environment variables, public tool counts, or GitHub Pages content changes.
2. Prefer the low-token router path (`find_tool`, `invoke_read_tool`,
   `invoke_tool`) for user-facing examples.
3. Run the smallest targeted tests that cover your change.
4. Run the local release gate before pushing:

```bash
uv run python scripts/validate_release.py --skip-rag
```

If you intentionally changed RAG/OpenAPI indexes or eval behavior, run the
relevant ingestion/eval commands instead of relying only on `--skip-rag`.

## Security reports

Do not publish exploitable details or secrets in issues or pull requests. Follow
[SECURITY.md](SECURITY.md) for vulnerability reports and credential exposure.
