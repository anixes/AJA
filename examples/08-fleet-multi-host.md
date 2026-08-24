# Example 08: Fleet — Multi-Host Mission Handover

**Capability**: Baton transfer of a running mission between hosts
**Difficulty**: Advanced
**Prerequisites**: Two AJA hosts (e.g. a cloud VPS orchestrator + home GPU box), shared `AJA_BATON_SECRET` on both, network reachability (HTTPS for remote endpoints). See `docs/operator/FLEET.md`.

## Objective

Offload a heavy mission from an orchestrator VPS to your home GPU worker and pull the result back.

## Steps

1. On BOTH hosts, set the same shared secret:

```bash
export AJA_BATON_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
# use the identical value on both machines
```

2. On the orchestrator VPS, capture the mission baton:

```bash
aja run "Long-running analysis mission..." 
# then capture:
aja baton capture --mission=<MISSION_ID>
```

This prints a 6-character code (e.g. `K3X9QZ`) and persists columnar Arrow state.

3. Transmit to the worker host:

```bash
aja baton transmit K3X9QZ --to https://home-worker.example.com:8443
```

4. On the home GPU worker, pick it up:

```bash
aja baton pickup K3X9QZ
```

The worker resumes with full conversation history and continues execution locally.

## Expected Output

Pickup reports the restored turn count and continues the mission without re-planning from scratch. Result flows back via the same mechanism or your normal telemetry channels.

## How It Works

Batons serialize mission history as Apache Arrow columns (schema v2) enabling parse-free pickup: mmap plus lazy per-turn decoding keeps cold pickup ~18ms even at 10,000 turns. Transfers are HMAC-SHA256 signed over the raw body (`X-AJA-Signature`); receivers reject unsigned or tampered payloads, codes are strictly `^[A-Z0-9]{6}$`, and insecure transport is refused to non-local endpoints. A 10 MB receive cap guards against abuse.

## Troubleshooting

- **401/invalid signature**: secrets differ between hosts; re-export `AJA_BATON_SECRET`.
- **Insecure transport refused**: remote targets must be HTTPS (localhost is exempt).
- **Corrupt baton error**: file was torn mid-write; recapture rather than retrying pickup.
- **Cold start slow on remote**: ensure the baton file path was rewritten to local storage (handled automatically by receive).
