/**
 * AgentX Indexer
 * 
 * Builds the repo graph by walking files, parsing symbols, and storing edges.
 * Supports full re-index and incremental (changed-files-only) updates.
 */

import { readdirSync, statSync } from 'fs';
import path from 'path';
import { parseFile } from './parser.js';
import { GraphStore } from './store.js';
import type { RepoGraph, GraphNode, GraphEdge } from './types.js';

// Directories to skip during indexing
const IGNORE_DIRS = new Set([
  'node_modules', '.git', 'dist', 'build', '.next', '__pycache__',
  'coverage', '.cache', '.turbo', 'vendor', '.venv', 'venv',
  '.agentx', 'graphify-out', 'claude-code-reference',
]);

// Extensions to index
const INDEX_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.mjs', '.mts',
  '.py',
  '.json',
]);

// Files to skip even with valid extensions
const SKIP_FILES = new Set([
  'package-lock.json', 'yarn.lock', 'bun.lockb',
  'tsconfig.json', '.eslintrc.json',
]);

export class Indexer {
  private store: GraphStore;
  private cwd: string;

  constructor(cwd: string = process.cwd()) {
    this.cwd = cwd;
    this.store = new GraphStore(cwd);
  }

  /**
   * Full re-index: walk the entire project, parse all files, build graph.
   */
  async fullIndex(): Promise<RepoGraph> {
    const startTime = Date.now();
    const files = this.walkProject();

    console.log(`📂 Indexing ${files.length} files...`);

    const allNodes: GraphNode[] = [];
    const allEdges: GraphEdge[] = [];
    const fileHashes: Record<string, string> = {};

    for (const file of files) {
      const result = parseFile(file, this.cwd);
      allNodes.push(...result.nodes);
      allEdges.push(...result.edges);
      fileHashes[result.filePath] = result.hash;
    }

    // Resolve wildcard edges (e.g., "class:*:BaseClass" → actual node)
    this.resolveWildcards(allNodes, allEdges);

    const duration = Date.now() - startTime;
    const graph: RepoGraph = {
      version: 2,
      projectRoot: this.cwd,
      indexedAt: new Date().toISOString(),
      fileHashes,
      nodes: allNodes,
      edges: allEdges,
      stats: this.store.computeStats(allNodes, allEdges, duration),
    };

    this.store.save(graph);
    console.log(`✅ Indexed: ${graph.stats.totalNodes} nodes, ${graph.stats.totalEdges} edges in ${duration}ms`);

    return graph;
  }

  /**
   * Incremental index: only reparse files that changed since last index.
   * Uses content hashes to detect changes.
   */
  async incrementalIndex(): Promise<{ graph: RepoGraph; changed: number; total: number }> {
    const existing = this.store.load();
    if (!existing) {
      const graph = await this.fullIndex();
      return { graph, changed: graph.stats.totalFiles, total: graph.stats.totalFiles };
    }

    const startTime = Date.now();
    const currentFiles = this.walkProject();
    const previousHashes = existing.fileHashes;

    // Detect changed, new, and deleted files
    const changedFiles: string[] = [];
    const newHashes: Record<string, string> = {};

    for (const file of currentFiles) {
      const result = parseFile(file, this.cwd);
      const prevHash = previousHashes[result.filePath];

      if (!prevHash || prevHash !== result.hash) {
        changedFiles.push(result.filePath);
      }
      newHashes[result.filePath] = result.hash;
    }

    // Detect deleted files
    const currentFileSet = new Set(currentFiles.map(f => {
      const rel = path.relative(this.cwd, path.resolve(this.cwd, f)).replace(/\\/g, '/');
      return rel;
    }));
    for (const prevFile of Object.keys(previousHashes)) {
      if (!currentFileSet.has(prevFile)) {
        changedFiles.push(prevFile);
      }
    }

    if (changedFiles.length === 0) {
      console.log('✅ No changes detected. Graph is up to date.');
      return { graph: existing, changed: 0, total: currentFiles.length };
    }

    console.log(`🔄 Updating ${changedFiles.length} changed files...`);

    // Reparse only changed files
    const newNodes: GraphNode[] = [];
    const newEdges: GraphEdge[] = [];
    const updatedHashes: Record<string, string> = {};

    for (const file of changedFiles) {
      if (currentFileSet.has(file)) {
        const result = parseFile(file, this.cwd);
        newNodes.push(...result.nodes);
        newEdges.push(...result.edges);
        updatedHashes[file] = result.hash;
      }
      // Deleted files: no new nodes/edges, hash will be removed by merge
    }

    // Resolve wildcards in new edges
    const allNodes = [
      ...existing.nodes.filter(n => !changedFiles.includes(n.filePath)),
      ...newNodes,
    ];
    this.resolveWildcards(allNodes, newEdges);

    const graph = this.store.merge(existing, changedFiles, newNodes, newEdges, updatedHashes);
    graph.stats.indexDurationMs = Date.now() - startTime;

    this.store.save(graph);
    console.log(`✅ Updated: ${changedFiles.length} files in ${graph.stats.indexDurationMs}ms`);

    return { graph, changed: changedFiles.length, total: currentFiles.length };
  }

  /**
   * Get the current graph (load from disk, or build if none exists).
   */
  async getOrBuildGraph(): Promise<RepoGraph> {
    const existing = this.store.load();
    if (existing) return existing;
    return this.fullIndex();
  }

  /**
   * Get the store instance.
   */
  getStore(): GraphStore {
    return this.store;
  }

  // ── Private ─────────────────────────────────────────────────

  private walkProject(): string[] {
    const files: string[] = [];
    this.walkDir(this.cwd, files);
    return files;
  }

  private walkDir(dir: string, files: string[]): void {
    try {
      for (const entry of readdirSync(dir)) {
        if (IGNORE_DIRS.has(entry) || entry.startsWith('.')) continue;
        const full = path.join(dir, entry);
        const st = statSync(full);

        if (st.isDirectory()) {
          this.walkDir(full, files);
        } else if (st.isFile()) {
          const ext = path.extname(entry).toLowerCase();
          if (!INDEX_EXTENSIONS.has(ext)) continue;
          if (SKIP_FILES.has(entry)) continue;
          if (st.size > 500_000) continue; // Skip huge files
          files.push(full);
        }
      }
    } catch {}
  }

  /**
   * Resolve wildcard edges like "class:*:BaseClass" to actual node IDs.
   */
  private resolveWildcards(nodes: GraphNode[], edges: GraphEdge[]): void {
    for (const edge of edges) {
      if (edge.target.includes(':*:')) {
        const targetName = edge.target.split(':*:')[1];
        const actualNode = nodes.find(n => n.name === targetName);
        if (actualNode) {
          edge.target = actualNode.id;
        }
      }
      if (edge.source.includes(':*:')) {
        const sourceName = edge.source.split(':*:')[1];
        const actualNode = nodes.find(n => n.name === sourceName);
        if (actualNode) {
          edge.source = actualNode.id;
        }
      }
    }
  }
}
