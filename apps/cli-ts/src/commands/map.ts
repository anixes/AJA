/**
 * agentx map [path]
 * 
 * Index the repo and display the knowledge graph summary.
 * First run: full index. Subsequent: incremental update.
 */

import { Indexer, GraphQuery } from '../graph/index.js';

export interface MapOptions {
  full?: boolean;    // Force full re-index
  json?: boolean;    // Output raw JSON
  verbose?: boolean;
}

export async function mapCommand(targetPath: string, opts: MapOptions): Promise<void> {
  const cwd = targetPath === '.' ? process.cwd() : targetPath;
  const indexer = new Indexer(cwd);

  console.log('🧠 AgentX Repo Brain\n');

  let graph;
  if (opts.full) {
    graph = await indexer.fullIndex();
  } else {
    const result = await indexer.incrementalIndex();
    graph = result.graph;
    if (result.changed > 0) {
      console.log(`   (${result.changed}/${result.total} files updated)\n`);
    }
  }

  if (opts.json) {
    console.log(JSON.stringify(graph.stats, null, 2));
    return;
  }

  // Display the summary
  const query = new GraphQuery(graph);
  console.log(query.repoSummary());

  // Show key symbols
  const files = graph.nodes.filter(n => n.kind === 'file');
  const functions = graph.nodes.filter(n => n.kind === 'function');
  const classes = graph.nodes.filter(n => n.kind === 'class');
  const routes = graph.nodes.filter(n => n.kind === 'route');
  const tests = graph.nodes.filter(n => n.kind === 'test');
  const schemas = graph.nodes.filter(n => n.kind === 'schema');

  if (classes.length > 0) {
    console.log('\n🏗️  Key Classes:');
    for (const cls of classes.slice(0, 15)) {
      console.log(`  ${cls.exported ? '📤' : '  '} ${cls.name} (${cls.filePath}:${cls.line})`);
    }
  }

  if (routes.length > 0) {
    console.log('\n🌐 API Routes:');
    for (const route of routes) {
      console.log(`  ${route.name} → ${route.filePath}:${route.line}`);
    }
  }

  if (tests.length > 0) {
    console.log(`\n🧪 Tests: ${tests.length} test cases across ${new Set(tests.map(t => t.filePath)).size} files`);
  }

  if (schemas.length > 0) {
    console.log('\n💾 Schemas/Models:');
    for (const schema of schemas) {
      console.log(`  ${schema.name} (${schema.filePath})`);
    }
  }

  if (opts.verbose && functions.length > 0) {
    console.log(`\n⚡ Functions (${functions.length}):`);
    for (const fn of functions.slice(0, 30)) {
      console.log(`  ${fn.exported ? '📤' : '  '} ${fn.name}(${fn.signature || '...'}) — ${fn.filePath}:${fn.line}`);
    }
  }

  console.log(`\n💡 Graph stored at: .agentx/graph.json`);
}
