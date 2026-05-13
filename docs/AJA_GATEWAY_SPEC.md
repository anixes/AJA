# AJA Gateway Specification (Assistant of Joint Agents)

## 1. Overview [IMPLEMENTED]
The **AJA Gateway** is a high-fidelity communication bridge between the AgentX core and messaging platforms (primarily Telegram). It is designed to provide a "Premium Secretary" experience, ensuring interactions are resilient, professional, and non-intrusive.

## 2. Core Architectural Pillars

### 2.1 Resilient Connection [DONE]
- [x] **Adaptive Polling**: Implemented exponential back-off in `gateway/telegram.py`.
- [x] **Draining Mode**: Supported via lifecycle hooks in `UnifiedGateway`.
- [x] **Freshness Gate**: Integrated into polling loop logic.
- [x] **Security Whitelisting**: Messages restricted to `TELEGRAM_ALLOWED_USER_ID` from `.env`.

### 2.2 Mobile-First Rendering [DONE]
- [x] **MD-to-Mobile Renderer**: `MobileMDRenderer` in `gateway/render.py` converts tables to bullet lists.
- [x] **Notification Management**: 
    - [x] **Silent Mode**: Progress updates use `disable_notification=True`.
    - [x] **Priority Pings**: User turns and critical errors use standard notification pings.

### 2.3 Media & Context Enrichment [DONE]
- [x] **Vision Integration**: `VisionBridge` in `gateway/vision.py` provides semantic description placeholders.
- [x] **State Persistence**: Store all gateway session state in LanceDB (`gateway_sessions.lance`) using Apache Arrow for high-speed, zero-copy retrieval.

## 3. Implementation Details

### 3.1 Base Classes [DONE]
- `BasePlatformAdapter`: Standardized interface in `gateway/base.py`.
- `UnifiedGateway`: Main orchestrator in `gateway/orchestrator.py`.

### 3.2 Telegram Specifics [DONE]
- Uses `python-telegram-bot` (v20+).
- Resilient polling loop with automatic retry logic.

## 4. Branding & Persona [DONE]
- [x] **Rebranding**: All modules use `aja_` or `agentx_` naming conventions.
- [x] **Persona**: AgentX core responds as the "Assistant of Joint Agents".
- [x] **Config Integration**: Automatically loads `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_USER_ID` from `.env`.
