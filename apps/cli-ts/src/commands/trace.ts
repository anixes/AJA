/**
 * agentx trace <query>
 * 
 * Trace connections between symbols or concepts in the codebase.
 * Uses graph BFS to find relationship paths.
 * 
 * Examples:
 *   agentx trace "login bug"       — find symbols related to login
 *   agentx trace "auth -> router"   — trace path from auth to router
 */

import { Indexer, GraphQuery } from '../graph/index.js';

export interface TraceOptions {
  depth?: number;
  verbose?: boolean;
}

export async function traceCommand(query: string, opts: TraceOptions): Promise<void> {
  const cwd = process.cwd();
  const indexer = new Indexer(cwd);
  const graph = await indexer.getOrBuildGraph();
  const gq = new GraphQuery(graph);

  // Check if it's a "from -> to" trace
  const arrowMatch = query.match(/^(.+?)\s*(?:->|→|to)\s*(.+)$/i);

  if (arrowMatch) {
    const from = arrowMatch[1].trim();
    const to = arrowMatch[2].trim();
    console.log(`🔍 Tracing: ${from} → ${to}\n`);

    const result = gq.trace(from, to);
    if (!result) {
      console.log('❌ No path found between these symbols.');
      console.log('\nTry broader terms or check available symbols with: agentx map .');
      return;
    }

    console.log(`📍 Path found (${result.path.length} nodes):\n`);
    for (let i = 0; i < result.path.length; i++) {
      const node = result.path[i];
      const prefix = i === 0 ? '🟢' : i === result.path.length - 1 ? '🔴' : '  │';
      console.log(`${prefix} [${node.kind}] ${node.name}`);
      console.log(`     ${node.filePath}${node.line ? `:${node.line}` : ''}`);
      if (i < result.edges.length) {
        console.log(`  │  ──${result.edges[i].kind}──>`);
      }
    }

    console.log(`\n${result.summary}`);
    return;
  }

  // Single query: find related symbols and connections
  console.log(`🔍 Tracing: "${query}"\n`);

  const matches = gq.search(query, 5);
  if (matches.length === 0) {
    console.log('❌ No matching symbols found.');
    return;
  }

  console.log(`Found ${matches.length} matching symbols:\n`);

  for (const match of matches) {
    console.log(`  📌 [${match.kind}] ${match.name}`);
    console.log(`     ${match.filePath}${match.line ? `:${match.line}` : ''}`);

    // Show connections
    const related = gq.related(match.name, 8);
    if (related.length > 0) {
      console.log('     Connections:');
      for (const rel of related.slice(0, 5)) {
        console.log(`       → [${rel.kind}] ${rel.name} (${rel.filePath})`);
      }
    }
    console.log();
  }
}
