/**
 * AgentX Graph Store
 * 
 * Persists the repo graph to .agentx/graph.json
 * Supports atomic reads/writes with corruption recovery.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync, unlinkSync } from 'fs';
import path from 'path';
import type { RepoGraph, GraphNode, GraphEdge, GraphStats } from './types.js';

const GRAPH_DIR = '.agentx';
const GRAPH_FILE = 'graph.json';

export class GraphStore {
  private graphPath: string;
  private cwd: string;

  constructor(cwd: string = process.cwd()) {
    this.cwd = cwd;
    const dir = path.join(cwd, GRAPH_DIR);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    this.graphPath = path.join(dir, GRAPH_FILE);
  }

  /**
   * Load the graph from disk. Returns null if no graph exists.
   */
  load(): RepoGraph | null {
    if (!existsSync(this.graphPath)) return null;

    try {
      const raw = readFileSync(this.graphPath, 'utf8');
      const data = JSON.parse(raw) as RepoGraph;
      // Validate structure
      if (!data.nodes || !data.edges || !data.version) return null;
      return data;
    } catch {
      // Corrupted — remove and return null
      try { unlinkSync(this.graphPath); } catch {}
      return null;
    }
  }

  /**
   * Save the graph to disk atomically (write to temp, then rename).
   */
  save(graph: RepoGraph): void {
    const tmpPath = this.graphPath + '.tmp';
    try {
      writeFileSync(tmpPath, JSON.stringify(graph), 'utf8');
      // Atomic rename
      const { renameSync } = require('fs');
      renameSync(tmpPath, this.graphPath);
    } catch {
      // Fallback: direct write
      writeFileSync(this.graphPath, JSON.stringify(graph), 'utf8');
      try { unlinkSync(tmpPath); } catch {}
    }
  }

  /**
   * Create a fresh empty graph.
   */
  createEmpty(): RepoGraph {
    return {
      version: 2,
      projectRoot: this.cwd,
      indexedAt: new Date().toISOString(),
      fileHashes: {},
      nodes: [],
      edges: [],
      stats: this.emptyStats(),
    };
  }

  /**
   * Merge incremental results into the existing graph.
   * Removes old nodes/edges for changed files, adds new ones.
   */
  merge(existing: RepoGraph, changedFiles: string[], newNodes: GraphNode[], newEdges: GraphEdge[], newHashes: Record<string, string>): RepoGraph {
    // Remove old data for changed files
    const changedSet = new Set(changedFiles);
    const filteredNodes = existing.nodes.filter(n => !changedSet.has(n.filePath));
    const filteredEdges = existing.edges.filter(e => {
      const sourceNode = existing.nodes.find(n => n.id === e.source);
      return !sourceNode || !changedSet.has(sourceNode.filePath);
    });

    // Merge new data
    const mergedNodes = [...filteredNodes, ...newNodes];
    const mergedEdges = [...filteredEdges, ...newEdges];
    const mergedHashes = { ...existing.fileHashes, ...newHashes };

    // Remove hashes for deleted files
    for (const file of changedFiles) {
      if (!newHashes[file]) delete mergedHashes[file];
    }

    return {
      ...existing,
      indexedAt: new Date().toISOString(),
      fileHashes: mergedHashes,
      nodes: mergedNodes,
      edges: mergedEdges,
      stats: this.computeStats(mergedNodes, mergedEdges, 0),
    };
  }

  /**
   * Compute graph statistics.
   */
  computeStats(nodes: GraphNode[], edges: GraphEdge[], durationMs: number): GraphStats {
    return {
      totalFiles: nodes.filter(n => n.kind === 'file').length,
      totalNodes: nodes.length,
      totalEdges: edges.length,
      totalFunctions: nodes.filter(n => n.kind === 'function' || n.kind === 'method').length,
      totalClasses: nodes.filter(n => n.kind === 'class').length,
      totalRoutes: nodes.filter(n => n.kind === 'route').length,
      totalTests: nodes.filter(n => n.kind === 'test').length,
      totalSchemas: nodes.filter(n => n.kind === 'schema').length,
      indexDurationMs: durationMs,
    };
  }

  private emptyStats(): GraphStats {
    return { totalFiles: 0, totalNodes: 0, totalEdges: 0, totalFunctions: 0, totalClasses: 0, totalRoutes: 0, totalTests: 0, totalSchemas: 0, indexDurationMs: 0 };
  }

  /**
   * Get the path to the graph file.
   */
  getGraphPath(): string {
    return this.graphPath;
  }

  /**
   * Check if a graph exists.
   */
  exists(): boolean {
    return existsSync(this.graphPath);
  }
}
