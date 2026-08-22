# AJA Fleet — Multi-Host Worker Deployment

Run AJA missions across multiple machines by transferring mission state
("batons") between an orchestrator host and worker hosts over HTTP, secured
with HMAC-SHA256.

## Architecture

```
┌──────────────┐   POST /baton/receive    ┌──────────────────┐
│ Orchestrator │ ───────────────────────► │   Worker Host    │
│  (mission)   │  X-AJA-Signature: HMAC   │ picks up + runs  │
└──────────────┘      (SHA-256 of body)   └──────────────────┘
```

- **Capture**: the orchestrator serializes mission state into an Apache Arrow
  baton (`BatonManager.capture()`), cached in RAM and persisted under
  `<DATA_DIR>/batons/`.
- **Transmit**: `transmit_baton(code, endpoint_url)` POSTs the metadata +
  base64 Arrow state to a worker's `/baton/receive` endpoint.
- **Receive**: the worker verifies the HMAC signature, validates the baton
  code (`^[A-Z0-9]{6}$`), enforces a 10 MB cap, then stores + caches the baton.
- **Pickup**: `pickup(code)` memory-maps the Arrow table back into live state,
  restoring the `trace_id` for observability lineage.

## Security model

| Control | Mechanism |
|---------|-----------|
| Authentication | HMAC-SHA256 over the raw request body; key = `AJA_BATON_SECRET` |
| Signature header | `X-AJA-Signature` |
| Transport | HTTPS enforced for non-local endpoints |
| Path safety | Baton codes strictly validated before any filesystem use |
| DoS | 10 MB payload cap; strict base64 validation |
| Replay resistance | Codes are generated via `secrets` (unpredictable); TTL-based cleanup |

Both hosts MUST share the same secret. Generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Setup

### On every host

```bash
# .env
AJA_BATON_SECRET=<shared-hex-secret>
```

### Orchestrator side

Nothing extra — missions produce batons automatically when workers are
dispatched across runs.

### Worker host

1. Ensure port reachability for the bridge API (the `/baton/receive` route is
   registered by `aja.api.bridge`).
2. Start the bridge:

```bash
python -m aja gateway   # or your process manager equivalent
```

3. Confirm posture with `aja doctor` — the startup checks verify the baton
   secret is configured.

## Transferring a mission

From the orchestrator:

```python
from aja.runtime.handover import BatonManager

mgr = BatonManager()
code = mgr.capture(objective, orchestrator_state)
ok = mgr.transmit_baton(code, "https://worker-host.example.com/baton/receive")
```

On the worker, pick the mission up:

```python
state = mgr.pickup(code)   # thaws objective/history/metadata incl. trace_id
```

Or let the autonomous loop consume it — workers poll `DATA_DIR/batons/`.

## Verification

The full loop (capture → transmit-format → signed receive → pickup, including
rejection of unsigned/tampered transfers) is covered in
`tests/python/integration/test_fleet_loop.py`.

```bash
py -3.12 -m pytest tests/python/integration/test_fleet_loop.py -q
```
