/**
 * Shared options for all CLI commands.
 */

import type { ExecutionMode } from '../engine/executionModes.js';

export interface CommandOptions {
  mode: ExecutionMode;
  model?: string;
  provider?: string;
  stream?: boolean;
  showCost?: boolean;
  verbose?: boolean;
}

/**
 * Print a cost summary line to stdout.
 */
export function printCost(tracker: { formatSessionSummary(): string }): void {
  console.log(`\n💰 ${tracker.formatSessionSummary()}`);
}

/**
 * Create a GatewayClient configured for the given options.
 */
export async function createClient(opts: CommandOptions) {
  const { GatewayClient } = await import('../services/gatewayClient.js');
  const client = new GatewayClient();

  // Override default provider if specified
  if (opts.provider) {
    try {
      client.getRegistry().setDefault(opts.provider);
    } catch {}
  }

  return client;
}

/**
 * Retrieve graph context for LLM injection.
 * Loads the graph, retrieves context for the prompt, and formats it.
 */
export async function getGraphContext(prompt: string, maxTokens: number = 8000): Promise<string> {
  try {
    const { GraphStore } = await import('../graph/store.js');
    const { ContextRetriever } = await import('../graph/retriever.js');
    
    const store = new GraphStore(process.cwd());
    const graph = store.load();
    if (!graph || graph.nodes.length === 0) return ''; // Graph not built
    
    const retriever = new ContextRetriever(graph, process.cwd());
    if (retriever.hasRelevantContext(prompt)) {
      const result = retriever.retrieve(prompt, maxTokens);
      return retriever.formatForLLM(result);
    }
  } catch (err) {
    // Silently ignore if graph isn't built or fails
  }
  return '';
}
