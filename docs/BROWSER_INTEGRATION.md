# Browser Integration & Internet Access (May 2026)

This document describes the technical architecture of the Agent browsing system, designed for high performance on local hardware with non-vision models.

## 🏗️ Architecture: The Hybrid Tiered Approach

To balance RAM usage and reliability, Agent uses a **Primary-Standby** browser model.

### 1. Primary Engine: Obscura (Rust)
*   **Path**: `E:\obscura\obscura.exe`
*   **Role**: Handles 90% of requests.
*   **Mechanism**: A high-performance, stateless fetcher that executes JavaScript and returns the DOM. 
*   **Benefit**: Extremely low RAM overhead (<50MB).

### 2. Standby Engine: Vercel Agent Browser (Chromium)
*   **Path**: Global `agent-browser` CLI
*   **Role**: Automatic failover.
*   **Trigger**: Activated if Obscura fails, returns <200 chars of content, or times out.
*   **Benefit**: Full Chromium rendering for complex SPAs (Single Page Apps).

---

## 👁️ Non-Vision Intelligence: Pseudo-Snapshots

Since local models are primarily text-based, the system transforms HTML into a **Semantic Map** before the model sees it.

### Interactive Markers
The distillation pipeline injects the following markers:
- `[@e1] [LINK: Text (URL: /target)]`
- `[@e2] [BUTTON: Label]`
- `[@e3] [INPUT: Placeholder (type)]`

### Example Distilled Output
```text
Welcome to Example News
[@e1] [LINK: Login (URL: /auth)]
[@e2] [LINK: Contact Us (URL: /contact)]

Latest Headlines:
- AI Agents reach new milestones in local inference.
[@e3] [INPUT: Search News (text)]
```

---

## 🛠️ Capability Reference

### `browser.search`
- **Description**: Performs a web search via DuckDuckGo Lite.
- **Inputs**: `{"query": "string"}`

### `browser.read`
- **Description**: Fetches and distills a URL.
- **Inputs**: `{"url": "string", "clean": true, "use_standby": true}`
- **Failover Logic**: 
    1. Try Obscura.
    2. If content length < 200 or error: Try Vercel Agent Browser.

### `browser.navigate`
- **Description**: Moves to a new URL. Currently an alias for `browser.read` with session-awareness planned for future phases.

---

## 🔧 Maintenance
- **Obscura Updates**: Managed via manual binary replacement in `E:\obscura`.
- **Standby Updates**: Run `npm install -g agent-browser; agent-browser install`.

*Documentation generated on 2026-05-11.*
