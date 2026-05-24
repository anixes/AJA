# Phase 07: AJA Gateway (Premium Telegram Secretary)

## Objective
Implement a high-fidelity Telegram gateway for the AJA persona, incorporating the resilience and UX strategies from `hermes-agent`.

## Wave 1: Foundation & Adapters
- [ ] **Task 1**: Implement `packages/aja-core/aja/gateway/base.py` with the `BasePlatformAdapter` and `MessageEvent` abstractions.
- [ ] **Task 2**: Implement `packages/aja-core/aja/gateway/telegram.py` (The Telegram Adapter) with resilient polling and adaptive back-off.
- [ ] **Task 3**: Setup state persistence in `.aja/gateway/` for session and delivery tracking.

## Wave 2: UX & Rendering
- [ ] **Task 4**: Implement the `MobileMDRenderer` to convert tables to bullet lists for Telegram.
- [ ] **Task 5**: Implement the **Notification Controller** (Silent vs. Priority pings).
- [ ] **Task 6**: Implement Vision Enrichment (Photo -> Text description) bridge.

## Wave 3: Integration & Launch
- [ ] **Task 7**: Create the `AjaGatewayRunner` to orchestrate the gateway lifecycle.
- [ ] **Task 8**: Connect the gateway to the `AJA` baton protocol for mission-aware messaging.
- [ ] **Task 9**: Verify with a Telegram Bot test run and mobile rendering audit.

## Success Criteria
- [ ] Gateway survives a 30s network disconnect and resumes polling.
- [ ] A mission with a Markdown table is received on mobile as a readable list.
- [ ] Tool call "thinking" messages do not trigger push notifications.
