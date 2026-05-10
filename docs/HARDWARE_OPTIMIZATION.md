# Hardware Optimization Guide (GTX 1650 Ti)

This document outlines the specific hardware-level tuning applied to AgentX to achieve maximum performance on 4GB VRAM hardware.

## 1. Inference Backend: Llama.cpp (Gold Standard)

The system uses a custom-tuned `llama-server` configuration located in `start_llama_gold.bat`.

### Key Parameters:
*   **`-ub 256` (Micro-batch)**: Optimized for the 1650 Ti's CUDA core count. This maximizes prompt processing speed (~350 t/s) without overloading the 4GB memory buffer.
*   **`-c 32768` (Context Window)**: This is the "Safe Ceiling." Beyond this, latency scales linearly into unusable territory (the "Linear Wall").
*   **`--context-shift`**: Allows the model to "forget" the oldest parts of a conversation efficiently without re-processing the entire prompt.
*   **`--cache-prompt`**: Keeps the initial "System Instructions" and "Baton Logic" in KV cache for instant response times.

## 2. Intelligent Memory Architecture

To prevent performance degradation during long agentic runs, AgentX implements a **Tiered Memory System**.

### Level 1: Active Context (0 - 5,000 Characters)
The swarm maintains raw task history for maximum precision. At this level, generation is instant (~88 TPS).

### Level 2: Auto-Summarization (5,000+ Characters)
The `gateway.py` monitor triggers an automatic "Compression Gate."
*   **Trigger**: Context length > 5,000 characters.
*   **Action**: The history is passed through a high-density summarizer.
*   **Result**: Redundant logs are stripped, and only key decisions/file paths are preserved. This resets the "Context Weight" and prevents the 1650 Ti from hitting high-latency bottlenecks.

## 3. GPU Management
The system uses `nvidia-smi` to lock GPU clocks:
*   **Base**: 1500 MHz
*   **Boost**: 1800 MHz
This prevents the GPU from "down-clocking" during thinking pauses, ensuring the first-token latency remains under 200ms.

---
*Last Updated: 2026-05-11*
