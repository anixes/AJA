/**
 * agentx impact <file|symbol>
 * 
 * Analyze the blast radius of changing a file or symbol.
 * Shows direct dependents, transitive dependents, and a risk score.
 * 
 * Examples:
 *   agentx impact user.ts
 *   agentx impact GatewayClient
 */

import { Indexer, GraphQuery } from '../graph/index.js';

export interface ImpactOptions {
  depth?: number;
  verbose?: boolean;
}

export async function impactCommand(target: string, opts: ImpactOptions): Promise<void> {
  const cwd = process.cwd();
  const indexer = new Indexer(cwd);
  const graph = await indexer.getOrBuildGraph();
  const gq = new GraphQuery(graph);

  console.log(`💥 Impact Analysis: "${target}"\n`);

  const result = gq.impact(target, opts.depth || 3);

  if (!result) {
    console.log('❌ No matching symbol found in the graph.');
    console.log('Run `agentx map .` first to index the repository.');
    return;
  }

  // Risk badge
  const riskColor = result.riskScore >= 70 ? '🔴' : result.riskScore >= 40 ? '🟡' : '🟢';
  console.log(`${riskColor} Risk Score: ${result.riskScore}/100`);
  console.log();

  // Source info
  console.log(`📌 Source: [${result.source.kind}] ${result.source.name}`);
  console.log(`   File:   ${result.source.filePath}${result.source.line ? `:${result.source.line}` : ''}`);
  console.log();

  // Direct dependents
  if (result.directDeps.length > 0) {
    const uniqueFiles = [...new Set(result.directDeps.map(n => n.filePath))];
    console.log(`⚡ Direct Dependents (${result.directDeps.length} symbols in ${uniqueFiles.length} files):`);
    for (const dep of result.directDeps.slice(0, 20)) {
      const icon = dep.kind === 'test' ? '🧪' : dep.kind === 'route' ? '🌐' : '  ';
      console.log(`  ${icon} [${dep.kind}] ${dep.name} — ${dep.filePath}`);
    }
    if (result.directDeps.length > 20) {
      console.log(`  ... and ${result.directDeps.length - 20} more`);
    }
    console.log();
  } else {
    console.log('⚡ No direct dependents found.\n');
  }

  // Transitive dependents
  if (result.transitiveDeps.length > 0) {
    const uniqueFiles = [...new Set(result.transitiveDeps.map(n => n.filePath))];
    console.log(`🔗 Transitive Dependents (${result.transitiveDeps.length} symbols in ${uniqueFiles.length} files):`);
    for (const dep of result.transitiveDeps.slice(0, 15)) {
      console.log(`     [${dep.kind}] ${dep.name} — ${dep.filePath}`);
    }
    if (result.transitiveDeps.length > 15) {
      console.log(`  ... and ${result.transitiveDeps.length - 15} more`);
    }
    console.log();
  }

  // Summary
  console.log(`${'─'.repeat(50)}`);
  console.log(result.summary);

  // Suggestions
  console.log(`\n💡 Suggestions:`);
  if (result.riskScore >= 70) {
    console.log('  • Consider breaking this change into smaller PRs');
    console.log('  • Add/update tests for all direct dependents');
    console.log('  • Request reviews from owners of affected modules');
  } else if (result.riskScore >= 40) {
    console.log('  • Review direct dependents for breaking changes');
    console.log('  • Run full test suite before merging');
  } else {
    console.log('  • Low-risk change — standard review process');
  }
}
