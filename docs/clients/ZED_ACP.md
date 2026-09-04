# Zed IDE & Agent Client Protocol (ACP) Integration

AJA implements the open **Agent Client Protocol (ACP)** specification co-developed by Zed Industries and JetBrains. This allows Zed IDE and JetBrains editors to use AJA directly as an in-editor AI coding agent.

---

## 1. Running the ACP Server

AJA exposes its ACP server over standard I/O using JSON-RPC 2.0:

```powershell
aja acp
```

When started, the server listens for line-delimited JSON-RPC requests on `stdin` and emits responses and progress notifications (`session/update`) on `stdout`. Diagnostic logs are safely emitted to `stderr`.

---

## 2. Supported ACP Methods

| Method | Direction | Description |
| :--- | :--- | :--- |
| `initialize` | Client $\to$ AJA | Negotiates protocol version (`2024-11-05`) and capabilities |
| `session/new` | Client $\to$ AJA | Creates a new session tied to editor workspace roots |
| `session/prompt` | Client $\to$ AJA | Submits a user prompt, executes the AJA agent loop, and streams status |
| `session/update` | AJA $\to$ Client | Live progress notification (idle, running, tool execution) |
| `session/cancel` | Client $\to$ AJA | Cancels the active generation or tool task |

---

## 3. Dynamic Context Providers (`@` Syntax)

In direct sessions and ACP prompts, AJA supports high-precision context tokens inspired by Zed's context servers:

* **`@file:<path>`**: Extracts and line-numbers the specified file.
  * *Example*: `"Review the error handling in @file:libs/aja-core/aja/models/gbnf.py"`
* **`@symbol:<name>`**: Uses Python's AST parser to locate and extract class or function definitions across the workspace without reading the entire file.
  * *Example*: `"Refactor @symbol:run_direct_loop to support timeout limits"`
* **`@diff`**: Injects uncommitted git changes into the context block.
  * *Example*: `"Analyze @diff and generate commit notes"`
* **`@diagnostics`**: Injects workspace syntax and linter status.
  * *Example*: `"Run @diagnostics and fix any broken imports"`
