/**
 * AgentX Graph Query Engine
 * 
 * Answers structural questions about the codebase:
 *   - trace: find paths between symbols
 *   - impact: what breaks if I change this?
 *   - related: what's connected to this?
 *   - summary: high-level repo overview
 */

import type { RepoGraph, GraphNode, GraphEdge, TraceResult, ImpactResult } from './types.js';

export class GraphQuery {
  private nodes: Map<string, GraphNode>;
  private adjacency: Map<string, Array<{ target: string; edge: GraphEdge }>>;
  private reverseAdj: Map<string, Array<{ source: string; edge: GraphEdge }>>;

  constructor(private graph: RepoGraph) {
    this.nodes = new Map(graph.nodes.map(n => [n.id, n]));

    // Build adjacency lists
    this.adjacency = new Map();
    this.reverseAdj = new Map();
    for (const edge of graph.edges) {
      if (!this.adjacency.has(edge.source)) this.adjacency.set(edge.source, []);
      this.adjacency.get(edge.source)!.push({ target: edge.target, edge });
      if (!this.reverseAdj.has(edge.target)) this.reverseAdj.set(edge.target, []);
      this.reverseAdj.get(edge.target)!.push({ source: edge.source, edge });
    }
  }

  // ── Search ──────────────────────────────────────────────────

  /**
   * Find nodes matching a query string (fuzzy name match).
   */
  search(query: string, limit: number = 20): GraphNode[] {
    const q = query.toLowerCase();
    const scored: Array<{ node: GraphNode; score: number }> = [];

    for (const node of this.graph.nodes) {
      let score = 0;
      const name = node.name.toLowerCase();
      const filePath = node.filePath.toLowerCase();

      if (name === q) score = 100;
      else if (name.startsWith(q)) score = 80;
      else if (name.includes(q)) score = 60;
      else if (filePath.includes(q)) score = 40;
      else if (node.signature?.toLowerCase().includes(q)) score = 20;
      else continue;

      // Boost exported symbols
      if (node.exported) score += 5;
      // Boost files and classes (they're more important)
      if (node.kind === 'file') score += 3;
      if (node.kind === 'class') score += 2;

      scored.push({ node, score });
    }

    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map(s => s.node);
  }

  // ── Trace ───────────────────────────────────────────────────

  /**
   * Trace the relationship path between two symbols.
   * Uses BFS to find the shortest path.
   */
  trace(fromQuery: string, toQuery: string): TraceResult | null {
    const fromNodes = this.search(fromQuery, 3);
    const toNodes = this.search(toQuery, 3);
    if (fromNodes.length === 0 || toNodes.length === 0) return null;

    const from = fromNodes[0];
    const toSet = new Set(toNodes.map(n => n.id));

    // BFS
    const visited = new Set<string>();
    const queue: Array<{ nodeId: string; path: string[]; edges: GraphEdge[] }> = [
      { nodeId: from.id, path: [from.id], edges: [] }
    ];
    visited.add(from.id);

    while (queue.length > 0) {
      const current = queue.shift()!;
      if (toSet.has(current.nodeId)) {
        return {
          path: current.path.map(id => this.nodes.get(id)!).filter(Boolean),
          edges: current.edges,
          summary: this.formatTraceSummary(current.path, current.edges),
        };
      }

      // Explore forward edges
      for (const adj of this.adjacency.get(current.nodeId) || []) {
        if (!visited.has(adj.target)) {
          visited.add(adj.target);
          queue.push({
            nodeId: adj.target,
            path: [...current.path, adj.target],
            edges: [...current.edges, adj.edge],
          });
        }
      }
      // Also explore reverse edges (bidirectional search)
      for (const adj of this.reverseAdj.get(current.nodeId) || []) {
        if (!visited.has(adj.source)) {
          visited.add(adj.source);
          queue.push({
            nodeId: adj.source,
            path: [...current.path, adj.source],
            edges: [...current.edges, adj.edge],
          });
        }
      }

      if (current.path.length > 10) break; // Max depth
    }

    return null;
  }

  // ── Impact Analysis ─────────────────────────────────────────

  /**
   * Analyze the impact of changing a file or symbol.
   * Returns direct and transitive dependents.
   */
  impact(query: string, maxDepth: number = 3): ImpactResult | null {
    const matches = this.search(query, 1);
    if (matches.length === 0) return null;

    const source = matches[0];

    // Find all nodes in the same file
    const fileNodes = this.graph.nodes.filter(n => n.filePath === source.filePath);
    const fileNodeIds = new Set(fileNodes.map(n => n.id));

    // BFS for dependents (who depends on this?)
    const directDeps: GraphNode[] = [];
    const transitiveDeps: GraphNode[] = [];
    const visited = new Set<string>();

    // Level 1: direct dependents
    for (const nodeId of fileNodeIds) {
      for (const rev of this.reverseAdj.get(nodeId) || []) {
        if (!fileNodeIds.has(rev.source) && !visited.has(rev.source)) {
          visited.add(rev.source);
          const node = this.nodes.get(rev.source);
          if (node) directDeps.push(node);
        }
      }
    }

    // Level 2+: transitive dependents
    let frontier = directDeps.map(n => n.id);
    for (let depth = 1; depth < maxDepth && frontier.length > 0; depth++) {
      const nextFrontier: string[] = [];
      for (const nodeId of frontier) {
        for (const rev of this.reverseAdj.get(nodeId) || []) {
          if (!visited.has(rev.source) && !fileNodeIds.has(rev.source)) {
            visited.add(rev.source);
            const node = this.nodes.get(rev.source);
            if (node) {
              transitiveDeps.push(node);
              nextFrontier.push(node.id);
            }
          }
        }
      }
      frontier = nextFrontier;
    }

    // Risk score: based on number and kind of dependents
    const riskScore = Math.min(100, Math.round(
      (directDeps.length * 15) +
      (transitiveDeps.length * 5) +
      (directDeps.filter(n => n.kind === 'test').length * -10) + // Tests lower risk
      (directDeps.filter(n => n.kind === 'route').length * 20)   // Routes raise risk
    ));

    // Deduplicate by file
    const uniqueDirectFiles = [...new Set(directDeps.map(n => n.filePath))];
    const uniqueTransFiles = [...new Set(transitiveDeps.map(n => n.filePath))];

    return {
      source,
      directDeps,
      transitiveDeps,
      riskScore: Math.max(0, riskScore),
      summary: this.formatImpactSummary(source, uniqueDirectFiles, uniqueTransFiles, riskScore),
    };
  }

  // ── Related Symbols ─────────────────────────────────────────

  /**
   * Find symbols related to a query (neighbors in the graph).
   */
  related(query: string, limit: number = 20): GraphNode[] {
    const matches = this.search(query, 1);
    if (matches.length === 0) return [];

    const source = matches[0];
    const relatedIds = new Set<string>();

    // Forward neighbors
    for (const adj of this.adjacency.get(source.id) || []) {
      relatedIds.add(adj.target);
    }
    // Reverse neighbors
    for (const rev of this.reverseAdj.get(source.id) || []) {
      relatedIds.add(rev.source);
    }
    // Same-file siblings
    for (const node of this.graph.nodes) {
      if (node.filePath === source.filePath && node.id !== source.id) {
        relatedIds.add(node.id);
      }
    }

    return [...relatedIds]
      .map(id => this.nodes.get(id))
      .filter((n): n is GraphNode => n != null)
      .slice(0, limit);
  }

  // ── Repo Summary ────────────────────────────────────────────

  /**
   * Generate a high-level summary of the repository structure.
   */
  repoSummary(): string {
    const s = this.graph.stats;
    const lines: string[] = [
      `📊 Repository Graph Summary`,
      `${'─'.repeat(40)}`,
      `Files:      ${s.totalFiles}`,
      `Symbols:    ${s.totalNodes} (${s.totalFunctions} functions, ${s.totalClasses} classes)`,
      `Edges:      ${s.totalEdges}`,
      `Routes:     ${s.totalRoutes}`,
      `Tests:      ${s.totalTests}`,
      `Schemas:    ${s.totalSchemas}`,
      `Indexed:    ${this.graph.indexedAt}`,
      '',
    ];

    // Top connected files (hub files)
    const fileDegree = new Map<string, number>();
    for (const edge of this.graph.edges) {
      const sourceNode = this.nodes.get(edge.source);
      if (sourceNode?.kind === 'file') {
        fileDegree.set(sourceNode.filePath, (fileDegree.get(sourceNode.filePath) || 0) + 1);
      }
      const targetNode = this.nodes.get(edge.target);
      if (targetNode?.kind === 'file') {
        fileDegree.set(targetNode.filePath, (fileDegree.get(targetNode.filePath) || 0) + 1);
      }
    }

    const hubs = [...fileDegree.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);

    if (hubs.length > 0) {
      lines.push('🔗 Most Connected Files (Hubs):');
      for (const [file, degree] of hubs) {
        lines.push(`  ${degree.toString().padStart(3)} edges │ ${file}`);
      }
      lines.push('');
    }

    // Exported symbols count by kind
    const exportedByKind = new Map<string, number>();
    for (const node of this.graph.nodes) {
      if (node.exported && node.kind !== 'file') {
        exportedByKind.set(node.kind, (exportedByKind.get(node.kind) || 0) + 1);
      }
    }

    if (exportedByKind.size > 0) {
      lines.push('📤 Exported Symbols:');
      for (const [kind, count] of [...exportedByKind.entries()].sort((a, b) => b[1] - a[1])) {
        lines.push(`  ${count.toString().padStart(3)} ${kind}s`);
      }
    }

    return lines.join('\n');
  }

  // ── Helpers ─────────────────────────────────────────────────

  private formatTraceSummary(pathIds: string[], edges: GraphEdge[]): string {
    const parts: string[] = [];
    for (let i = 0; i < pathIds.length; i++) {
      const node = this.nodes.get(pathIds[i]);
      if (!node) continue;
      parts.push(`[${node.kind}] ${node.name}`);
      if (i < edges.length) {
        parts.push(` ──${edges[i].kind}──> `);
      }
    }
    return parts.join('');
  }

  private formatImpactSummary(source: GraphNode, directFiles: string[], transitiveFiles: string[], risk: number): string {
    const riskLabel = risk >= 70 ? '🔴 HIGH' : risk >= 40 ? '🟡 MEDIUM' : '🟢 LOW';
    const lines = [
      `Impact Analysis: ${source.name} (${source.filePath})`,
      `Risk: ${riskLabel} (${risk}/100)`,
      `Direct dependents: ${directFiles.length} files`,
      `Transitive dependents: ${transitiveFiles.length} files`,
    ];
    if (directFiles.length > 0) {
      lines.push('', 'Directly affected:');
      for (const f of directFiles.slice(0, 10)) lines.push(`  • ${f}`);
      if (directFiles.length > 10) lines.push(`  ... and ${directFiles.length - 10} more`);
    }
    return lines.join('\n');
  }
}
