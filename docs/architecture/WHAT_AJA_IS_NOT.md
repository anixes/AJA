# What AJA Runtime Is Not

To prevent architectural drift, hype-driven feature requests, and contributor misconception, it is critical to explicitly define what the AJA Runtime **is not**.

## 1. Not AGI or "Cognitive Superintelligence"
AJA is a local-first orchestration runtime. It provides deterministic infrastructure—memory, scheduling, inter-process communication (IPC), and execution sandboxes—to run software agents. It does not "think," nor does it possess magical problem-solving abilities outside of the models supplied to it.

## 2. Not a "Jarvis" Chatbot Wrapper
While AJA can run conversational agents, it is not fundamentally a chat application. AJA treats agentic workflows as **standard scheduled compute**. The Telegram, CLI, and Web UI layers are merely *clients* consuming runtime APIs, not the core product.

## 3. Not a Prompt Orchestration Framework
AJA is not a drop-in alternative for libraries like LangChain or LlamaIndex. It does not focus on chaining prompts or parsing unstructured text. AJA focuses on the **execution environment**—making sure that when an agent decides to run a command, that command is scheduled, isolated, executed, and traced securely.

## 4. Not Fully Deterministic (Yet)
While the `cron_scheduler.py` and `TraceStore` aim for strict determinism, execution currently relies on external factors (like Docker availability and host workspace state). Exact replayability and step-backward rollback remain aspirational until full copy-on-write filesystem overlays are implemented.

## 5. Not Fully Isolated Execution (Yet)
The `sandbox.py` subsystem mounts the host workspace natively into the Docker container (`-v HOST:WORKDIR`). A destructive command run by an agent inside the sandbox can still affect the host codebase. AJA does not yet provide true ephemeral workspaces.

## 6. Not a Distributed Cloud Orchestrator
While `handover.py` supports HTTP transmission of Arrow batons to remote workers, AJA is overwhelmingly optimized for single-node, **local-first** execution. It is not designed to replace Kubernetes or Temporal for massive parallel cloud scale.

---

*By accepting these constraints, we maintain an infrastructure-first design philosophy that values reliability over speculative capability.*
