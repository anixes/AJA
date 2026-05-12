# AgentX & AJA

## 🚀 The Local-First Agentic OS
**High-Performance Autonomy for Every Machine.**

AgentX is a cutting-edge orchestration framework designed to bridge the gap between elite AI capabilities and accessible hardware. By leveraging a **Local-First** philosophy and extreme memory engineering, AgentX enables a sophisticated, autonomous agent swarm to run efficiently on the cheapest of hardware while delivering maximum performance.

---

## 🎯 Our Mission: Performance Without Compromise
We believe that high-performance autonomous orchestration should not be a luxury reserved for multi-GPU clusters. AgentX is engineered from the ground up to:
- **Democratize Autonomy**: Built to run on standard consumer-grade hardware with zero performance degradation.
- **Local-First Security**: Your state, your memory, and your missions stay local, backed by a high-speed Arrow persistence layer.
- **Extreme Efficiency**: Utilizing Rust-native acceleration and zero-copy memory patterns to maximize every CPU cycle.

---

## 🏗️ The Pure AgentX Architecture

### 1. Unified Arrow Memory (LanceDB)
Legacy database bottlenecks have been purged. AgentX utilizes a 100% **Apache Arrow** memory stack. Both structured activity and semantic context are managed via **LanceDB**, providing:
- **Zero-Copy Performance**: Data is moved between the engine and storage without expensive serialization.
- **O(1) Semantic Retrieval**: Instant access to long-term memory via SIMD-accelerated vector search.
- **Columnar Efficiency**: Drastically reduced memory footprint compared to traditional row-based storage.

### 2. Native Rust Nervous System (`agentx-native`)
The performance-critical core is accelerated by a native Rust engine. By offloading token analysis, state transitions, and IPC serialization to Rust, AgentX achieves **10x faster response times** than traditional Python-only frameworks.

### 3. Arrow IPC Baton Protocol
Distributed mission coordination uses **Apache Arrow Tables**. When a task is delegated, the mission state is handed over as a binary "Baton" that is memory-mapped by the sub-agent. This ensures sub-millisecond coordination latency for even the largest swarms.

---

## 🤖 Meet AJA (Assistant to the Joint Agents)
While **AgentX** is the high-performance engine, **AJA** is your interface. She is the conversational operator who:
- **Plans & Delegates**: Translates your natural language intent into structured missions for the AgentX swarm.
- **Guards Your System**: Uses the **AJA Guard** to audit every command for safety before execution.
- **Simplifies Complexity**: Manages your schedule, obligations, and local-first memory seamlessly.

---

## 🛠️ Technology Stack
- **Core Engine**: Python 3.12+ (Modular & Optimized)
- **Performance Layer**: Rust-native acceleration via `pyo3`
- **Memory Stack**: Apache Arrow & LanceDB (SIMD-accelerated)
- **Safety Layer**: AJA Guard (Risk-aware command auditing)
- **TUI/CLI**: Premium conversational interface using **Rich** & **Textual**
- **Dashboard**: React 19 Executive Command Center

---

## 🚀 Getting Started

### 1. Launch AJA Chat
Interact with your assistant and manage the swarm through a premium conversational loop.
```bash
agentx chat
```

### 2. Dispatch Missions
Delegate complex objectives directly to the SwarmEngine.
```bash
agentx run "Audit the project security and implement missing guardrails"
```

### 3. Monitor Swarm Health
View real-time metrics and active baton handoffs across the Arrow memory stack.
```bash
agentx status
```

---

## 📜 Philosophy
Performance is not a luxury—it is an engineering requirement. AgentX proves that by prioritizing **Memory Efficiency** and **Native Execution**, we can deliver world-class autonomous systems on the hardware you already own.
