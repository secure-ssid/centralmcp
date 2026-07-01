## Summary

<!-- Describe the focused change and the user-facing behavior it affects. -->

## Checklist

- [ ] I used fake hosts, fake IDs, and redacted payloads in examples, tests, logs, and screenshots.
- [ ] I did not commit real credentials, tokens, tenant IDs, customer data, generated indexes, or local MCP config files.
- [ ] I updated matching docs when behavior, setup, environment variables, public tool counts, or GitHub Pages content changed.
- [ ] I kept user-facing examples on the low-token router path (`find_tool`, `invoke_read_tool`, `invoke_tool`) where possible.
- [ ] I ran targeted tests for the changed behavior.
- [ ] I ran `uv run python scripts/validate_release.py --skip-rag`, or noted why a different validation path was required.

## Security

For exploitable details or accidental credential exposure, do not use this pull
request. Follow [SECURITY.md](../SECURITY.md).

## Contributor notes

See [CONTRIBUTING.md](../CONTRIBUTING.md) for local setup, validation, and
no-secret contribution guidance.
