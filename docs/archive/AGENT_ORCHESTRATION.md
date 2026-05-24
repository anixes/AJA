# 🚀 Agent Orchestration (Baton Pattern)

The **Baton Pattern** is the core of Agent's reliability. It treats tasks as physical "batons" passed between processes.

### Workflow:
1.  **Task Creation**: The Orchestrator creates a `.baton` file in `temp_batons/` containing the task spec.
2.  **Process Forking**: A child process is spawned with the baton ID.
3.  **Autonomous Execution**: The worker executes the task, updating the baton state (Planning -> Executing -> Verifying).
4.  **Handoff**: On completion, the worker writes the result and terminates.
5.  **Re-entry**: The Orchestrator picks up the baton and presents the result to the User.

### Why Batons?
- **Crash Recovery**: If an agent crashes, the baton remains. A new agent can pick it up and resume exactly where it left off.
- **Multi-Agent Swarms**: Thousands of agents can work in parallel without state collisions.
- **Auditability**: Every step of the agent's "thinking" is saved to disk in the baton file.
