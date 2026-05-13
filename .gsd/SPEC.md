# Agent Hybrid Architecture Specification

## Overview
Agent is a high-performance autonomous orchestrator designed for local-first operations. It uses a hybrid architecture combining a Rust native core with a modular Python capability layer.

## Core Components

### 1. Native Core (agent-native)
Written in Rust to ensure memory safety, performance, and protocol fidelity.
- **Universal API Translation**: Low-overhead conversion between model vendor formats.
- **ACP Bridge**: Standardized agent-to-agent communication via JSON-RPC.
- **Semantic Engine**: Integrated LanceDB for high-speed vector retrieval.

### 2. Orchestration Layer (agent-core)
The central intelligence and capability management system.
- **Unified Gateway**: The primary hub for chat, memory, and tool execution.
- **Memory Tree**: SQLite relational storage for session-based activity tracking.
- **TokenJuice™**: Intelligent terminal log compaction for context optimization.

### 3. Capability Ecosystem
- **MCP Client**: Connection to Model Context Protocol servers.
- **Obsidian Connector**: Long-term knowledge storage in a local Markdown vault.
- **System Tools**: Terminal execution, browser automation, and app connectors.

## Implementation Workflow
- **Phase 1**: Core Scaffolding & Native Bridge.
- **Phase 2**: Protocol Layer (API Translation & ACP).
- **Phase 3**: Ecosystem Integration (MCP & Obsidian).
- **Phase 4**: Unified Memory System (Relational & Semantic).
