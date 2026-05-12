# Phase 2 Plan: Protocol Core (Rust)

## Objective
Implement the high-performance protocol handling and translation logic in Rust.

## Tasks
- [ ] **Task 2.1: Rust Universal API Model**
  - Port `UniversalRequest`, `UniversalItem`, and `ContentBlock` to Rust structs using `serde`.
  - Implement basic JSON serialization/deserialization.
- [ ] **Task 2.2: Rust Translator Bridge**
  - Implement `AnthropicTranslator` and `OpenAiTranslator` in Rust.
  - Expose `encode_request` and `decode_response` to Python via PyO3.
- [ ] **Task 2.3: Rust ACP Bridge**
  - Implement a `JsonRpcBridge` driver in Rust.
  - Handle `initialize`, `prompt`, and `ext_notification` methods.

## Verification
- Unit tests in Rust for translation logic.
- Python test script calling the Rust translators with complex payloads.
