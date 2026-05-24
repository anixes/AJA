# Knowledge Graph Exploration Guide

The `claude-code-reference` codebase has been fully indexed into a persistent knowledge graph. This allows for rapid exploration of relationships without re-analyzing the source files.

## Querying the Graph

To answer architectural questions or trace paths, use the following commands:

### 1. Simple Natural Language Query
```powershell
& "D:\ANACONDA py\python.exe" -m graphify query "How does X interact with Y?"
```

### 2. Find Shortest Path
```powershell
& "D:\ANACONDA py\python.exe" -m graphify path "SourceNode" "TargetNode"
```

### 3. Identify Key Hubs (God Nodes)
Check the `graphify-out/.graphify_analysis.json` file for the `gods` list, which shows the most connected components in the system.

## Visualizing the Graph
Open the following file in any web browser for an interactive 3D visualization:
`claude-code-reference/graphify-out/graph.html`

## Updating the Graph
If you modify the source code, run an incremental update to sync the graph (saves tokens):
```powershell
& "D:\ANACONDA py\python.exe" -m graphify --update
```

---
*Graphify environment is managed via the interpreter at `D:\ANACONDA py\python.exe`.*
