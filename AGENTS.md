# Cross-Agent Handoff Guide

This project is worked on in both Cursor and Claude. Use this file and `SESSION_CONTEXT.md` as the source of truth when switching tools.
If `SESSION_CONTEXT.md` does not exist, create it from `SESSION_CONTEXT.md.example`.

## Start-of-session checklist

1. Read `CLAUDE.md` for project conventions and API behavior.
2. Read `SESSION_CONTEXT.md` before making changes.
3. Confirm whether local-only files are ignored (`.mcp.json`, `config/credentials.yaml`, `.token_cache_*.json`).

## End-of-session checklist

1. Update `SESSION_CONTEXT.md`:
   - what changed
   - what was verified
   - what is still pending
   - exact next step
2. Keep entries concise and operational.
3. Do not include secrets, tokens, or private keys.

## Safety rules

- Never commit `.mcp.json` (local absolute paths).
- Never commit `config/credentials.yaml` or token caches.
- Prefer `.mcp.json.example` for shared configuration changes.
