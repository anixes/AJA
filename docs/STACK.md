# Technology Stack

> Last updated: 2026-05-20 (Product-Readiness Upgrades)

## Core Engine
| Technology | Version | Purpose |
|------------|---------|---------| 
| Python | **3.12.10** (Global) | Primary orchestration and logic layer |
| Rust | Edition 2021 | Native acceleration and memory-mapped IPC |
| Apache Arrow | v53 | Zero-copy data interchange format |
| LanceDB | v0.15 | Embedded vector database for long-term memory |
| Pydantic | v2.0+ | Config schema validation and data models |

> **⚠️ Python Runtime**: Always use `C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe`.  
> Do **not** use Anaconda Python (3.11.7) — it is a separate install at `D:\ANACONDA py\python.exe`.

## Python Dependencies (`libs/aja-core`)
| Package | Version | Purpose |
|---------|---------|---------| 
| Pydantic | ^2.0.0 | Config schema validation (`aja.json`) |
| aiohttp | Latest | Asynchronous HTTP requests for API providers |
| python-dotenv | Latest | Environment variable configuration |
| PyArrow | Latest | Python bindings for Arrow data handling |
| Rich | Latest | Beautiful CLI formatting and progress bars |
| Textual | Latest | Advanced TUI framework |
| FastAPI | Latest | WebSocket bridge and HTTP API |
| uvicorn | Latest | ASGI server for FastAPI |
| websockets | Latest | WebSocket protocol support |
| python-telegram-bot | Latest | AJA Telegram gateway |
| openai | Latest | OpenAI-compatible LLM provider |
| requests | Latest | Synchronous HTTP client |
| prompt_toolkit | Latest | Interactive CLI input handling |
| anyio | ^4.0 | Backend-agnostic async concurrency (used in tests) |
| psutil | Optional | System resource metrics (CPU, RAM, disk). Falls back to `os`/`shutil` stdlib if not installed. |

## Native Dependencies (`packages/aja-native`)
| Crate | Version | Purpose |
|-------|---------|---------| 
| PyO3 | 0.21 | Python bindings for Rust (GIL-free) |
| Tokio | 1.0 | Asynchronous runtime |
| Serde | 1.0 | High-performance serialization |
| tiktoken-rs | 0.6 | Fast token counting and encoding |
| lancedb | 0.15 | Native LanceDB engine integration |

## Dashboard (`apps/dashboard`)
| Technology | Version | Purpose |
|------------|---------|---------| 
| React | ^19.2.5 | Modern UI framework |
| Vite | ^8.0.9 | High-performance build tool |
| Tauri | ^2.11.0 | Cross-platform desktop/mobile wrapper |
| Tailwind CSS | ^4.2.4 | Utility-first styling with modern features |
| Framer Motion | ^12.38.0 | Fluid UI animations |
| Anime.js | ^3.2.2 | Micro-interaction engine |
| Zustand | ^5.0.3 | Lightweight state management |

## Infrastructure & Configuration
| Variable | Purpose | Required |
|----------|---------|----------| 
| `PYTHONPATH` | Directs Python to local `libs/aja-core` | Yes (Dev) |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Primary LLM provider | Yes |
| `TELEGRAM_TOKEN` | AJA Telegram bot gateway | Optional |
| `TELEGRAM_ALLOWED_USER_ID` | Whitelist for Telegram remote control | Optional |
| `LANCEDB_PATH` | Storage location for persistent brain | No (Defaults to `.aja/`) |
| `WS_PORT` | Dashboard telemetry port (8001) | No |
| `PYTHONIOENCODING` | Set to `utf-8` on Windows for emoji/rich output | Recommended |

## Key File Paths
| File | Purpose |
|------|---------|
| `aja.json` | Main project configuration (Pydantic-validated) |
| `libs/aja-core/aja/config_schema.py` | Pydantic schema for `aja.json` |
| `libs/aja-core/aja/observability/telemetry.py` | `TraceContextManager` — trace ID propagation |
| `libs/aja-core/aja/runtime/handover.py` | Arrow Baton IPC with trace header embedding |
| `libs/aja-core/aja/utils/diagnostics.py` | Systems health doctor |
| `libs/aja-core/aja/orchestration/swarm.py` | Swarm engine + dry-run simulation |
| `libs/aja-core/aja/main.py` | CLI entry point (setup, doctor, run, chat, status) |
| `.aja/security_audit.log` | Structured security event log (JSON lines) |
| `tests/python/` | Full Python test suite (119 tests, Python 3.12) |
