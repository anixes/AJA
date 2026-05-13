# 🚀 Agent Efficiency Guide: Max Work, Min Tokens

Agent is designed to operate autonomously while minimizing the "Token Tax" associated with large context windows. We achieve this through **Surgical Tools** and **Semantic Indexing**.

## 1. Zero-Token Indexing (`semantic_search`)
Instead of the agent reading every file to understand the project structure, we use a local Python-based AST/Regex scanner (`local_extractor.py`).

*   **Tool**: `semantic_search`
*   **Workflow**: The agent searches for a class or function name. The tool returns the **File Path** and **Line Number**.
*   **Token Savings**: **~95%** reduction compared to `ls -R` or recursive reads.

## 2. Surgical Peeking (`read_snippet`)
Full file reads are expensive and clutter the context window.

*   **Tool**: `read_snippet`
*   **Workflow**: The agent uses the line numbers from `semantic_search` to read only the relevant 10-20 lines of code.
*   **Token Savings**: **~90%** reduction for large source files.

## 3. The "Surgical Repair" Pattern
To fix a bug efficiently, follow this sequence:
1.  **Locate**: `semantic_search` for the suspect class/module.
2.  **Pinpoint**: `semantic_search` for the specific function.
3.  **Audit**: `read_snippet` the specific lines.
4.  **Patch**: `edit_file` with the targeted fix.

## 4. Local Mocking & Simulation
Testing agent logic shouldn't cost money.

*   **Feature**: `AGENTX_MOCK=true`
*   **Mechanism**: Swaps the LLM for a `MockQueryEngine` that follows a `simulation_playbook.json`.
*   **Use Case**: Verifying security gates, baton handoffs, and UI updates for $0.
