# HPE Support Ticket Draft — New Central events endpoint

Draft ticket text for HPE Aruba Networking support, documenting the
``/network-troubleshooting/v1/events`` and ``event-filters`` behaviors
observed on ``internal.api.central.arubanetworks.com`` in 2026-04.

Copy/paste into the HPE support portal or email to your Aruba TAC. The
``debugId`` values are the load-bearing bits — HPE can look those up in
their gateway logs.

---

## Subject

New Central NBAPI: `/network-troubleshooting/v1/events` and `event-filters`
consistently return `400 Bad Request` — need required-param documentation

## Environment

- **Region / cluster:** `internal.api.central.arubanetworks.com`
- **Auth:** OAuth2 client-credentials against `sso.common.cloud.hpe.com/as/token.oauth2`
- **Workspace ID:** `<redacted — provided in ticket>`
- **OAuth client ID used:** `<redacted — Central/source account>`
- **Application instance id on the token:** `<redacted>`

(Fill in the actual IDs from `config/credentials.yaml` when submitting the
ticket. They're intentionally not committed to the repo.)

Other endpoints on the same gateway (e.g. `/network-monitoring/v1/switches/{serial}`,
`/network-notifications/v1alpha1/alerts`, `/network-config/v1alpha1/cnac-mac-reg`)
work fine with the same token, so this isn't an auth or gateway-routing
issue.

## Problem

Every `GET` to the two event-related endpoints on the `network-troubleshooting`
service prefix returns HTTP 400 with a generic "Bad Request" body — no
hint in the response body about which parameter is missing or malformed.

### Reproduction

Minimal reproduction with `curl`:

```bash
TOKEN=...  # central_account token
for PATH in /network-troubleshooting/v1/event-filters \
            /network-troubleshooting/v1/events \
            /network-troubleshooting/v1alpha1/events \
            /network-troubleshooting/v1alpha1/event-filters ; do
    echo "=== $PATH ==="
    curl -sS \
        -H "Authorization: Bearer $TOKEN" \
        "https://internal.api.central.arubanetworks.com${PATH}?serialNumber=SG30LMR164&startTime=1776298338703&endTime=1776384738703"
    echo
done
```

All four variants return:

```json
{"errorCode":"HPE_GL_NETWORKING_ERROR_BAD_REQUEST","httpStatusCode":400,"message":"Bad Request, Your request was incorrect or incomplete. Please check and try again.","debugId":"<uuid>"}
```

The route clearly resolves (it's not 404) but the gateway rejects before
reaching a handler that would explain what's wrong.

### Parameter combinations tested (all 400)

All of these return the same `"Bad Request"`:

| `serialNumber` | `siteId` | `deviceType` | `startTime`/`endTime` | Result |
|:--|:--|:--|:--|:--|
| _(omitted)_ | _(omitted)_ | _(omitted)_ | _(omitted)_ | 400 |
| `SG30LMR164` | _(omitted)_ | _(omitted)_ | epoch ms | 400 |
| _(omitted)_ | `79244870000394240` | _(omitted)_ | epoch ms | 400 |
| `SG30LMR164` (as `serial`) | _(omitted)_ | _(omitted)_ | epoch ms | 400 |
| `SG30LMR164` (as `deviceSerial`) | _(omitted)_ | _(omitted)_ | epoch ms | 400 |
| `SG30LMR164` (as `deviceId`) | _(omitted)_ | _(omitted)_ | epoch ms | 400 |
| `SG30LMR164` | _(omitted)_ | `SWITCH` | epoch ms | 400 |

## debugId values for tracing

These are recent reproductions on the prod gateway. Please correlate
against the Central/MRT service logs:

- `d9f79f98f593cd24c0925c180beeb763`
- `53d8fd02350b034e7d8590b062f7bc36`
- `b4801fc65834856c3cdad9746e77e75d`
- `6552848b08a2d3720e8b0a4481f453be`
- `cc1fa8a409c81a21230de4d181a8ec44`
- `ad9625cd93f7660077c36b05b24b8f90`

(Add new debugIds as you run fresh repros.)

## Asks

1. **Publish / send the canonical request shape** for
   `/network-troubleshooting/v1/events` and
   `/network-troubleshooting/v1/event-filters`. Specifically:
   - Which query parameters are **required** vs optional?
   - Exact parameter **names** (our trial set: `serialNumber`, `siteId`,
     `deviceType`, `deviceSerial`, `deviceId`, `serial` — none
     accepted).
   - Expected response envelope for successful 200 (`items[]`?
     `events[]`? top-level fields?).
2. **Extend the 400 error body** to name the missing/bad parameter. The
   current message is unactionable without TAC round-trips; structured
   error fields like `field`, `reason`, or a `details` array would let
   integrators self-serve.
3. **Document `v1` vs `v1alpha1`** for these two endpoints. Peer
   integrations and our own analysis show both exist, but it isn't
   clear which is the supported/stable path. The
   [Aruba Dev Hub reference](https://developer.arubanetworks.com/new-central/reference)
   doesn't surface either under the ``network-troubleshooting`` sidebar.

## Why we care

Event count and event listing are core monitoring primitives for any
AI-assistant or SRE integration against New Central — we wrap Central's
NBAPI in an MCP (Model Context Protocol) server that Claude agents use
for day-1 operations. Every other service prefix we've probed on this
gateway (`network-monitoring`, `network-config`, `network-notifications`,
`network-services`) works; the events endpoint is the one gap, and
without it the agent can't answer "was this device flapping last night?"

## Workaround in place today

We flag the endpoint as known-broken in our client
(`mcp_servers/monitoring.py::get_events_count`), try the peer-consensus
path first (``event-filters``), fall back to the legacy path, and
surface the full 400 body in the tool's ``errors`` list so callers see
the real response.

## Environment details (if useful)

- Python 3.13.2, ``requests`` 2.x
- No proxy or corporate MITM involved
- Token re-minted fresh between each failing request to rule out
  staleness
- Same workspace, same token works against
  `GET /network-monitoring/v1alpha1/device-inventory?limit=5`
  in the same session.

---

**End of ticket draft.**
