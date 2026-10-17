# AJA Runtime Operator Console (Web Dashboard)

This package contains the interactive Web Operator Console for **AJA (Assistant of Joint Agents)**. It provides a visual dashboard for monitoring and controlling autonomous agent swarms, durable execution pipelines, and priority task desks.

---

## Features

- **Priority Desk & Task Kanban:** Real-time visual tracking of agent tasks across states (`PENDING`, `RUNNING`, `WAITING_APPROVAL`, `COMPLETED`, `FAILED`).
- **Interactive HTN Plan Tree DAG:** Dynamic visualization of Hierarchical Task Network (HTN) mission plans with step-by-step simulation and live operator inspection.
- **Operator-in-the-Loop (OITL) Approvals:** Safe inspection and modification of parameters for high-risk autonomous agent actions (`risk >= 0.7`) before execution.
- **AJA Chat Studio:** Conversational interface with slash-command autocompletion (`/todo`, `/swarm`, `/doctor`, `/status`, `/models`).
- **Telemetry & Event Log:** Live event streaming, system diagnostics, and worker capability registries.

---

## Directory Structure

```
web/
├── public/
│   └── index.html          # Standalone client application with Tailwind CSS and dark-mode design
├── server.ts               # Express 5 + TypeScript runtime bridge with WebSocket & Gemini API support
├── package.json            # Node.js dependencies (@google/genai, express, ws, cors, dotenv)
├── tsconfig.json           # TypeScript configuration
├── metadata.json           # AI Studio app metadata
├── AI_STUDIO_SPEC.md       # Full design specification and architectural blueprints
└── README.md               # Getting started and integration documentation
```

---

## Getting Started

### Prerequisites
- Node.js (v20+ recommended)
- npm or bun

### Installation
```bash
cd web
npm install
```

### Environment Configuration
Create a `.env` file in the `web` directory (or use root `.env`):
```env
PORT=3000
GEMINI_API_KEY=your_gemini_api_key
```

### Running the Dashboard
```bash
npm run dev
```
The console will be accessible at: `http://localhost:3000`

---

## Architecture & Integration with AJA Core

The Web Operator Console can operate in two modes:
1. **Standalone / Prototyping Mode:** Uses `server.ts` with in-memory stores and direct `@google/genai` API calls.
2. **Integrated Core Mode:** The static assets in `web/public/` can be served directly by AJA's FastAPI bridge (`libs/aja-core/aja/api/bridge.py`), connecting the visual console to the actual Python agent execution substrate.
