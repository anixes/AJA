/**
 * agentx ask "prompt"
 * 
 * Single-shot query: send prompt → get response → exit.
 * Supports streaming output and cost display.
 */

import type { CommandOptions } from './shared.js';
import { createClient, printCost, getGraphContext } from './shared.js';

export async function askCommand(prompt: string, opts: CommandOptions): Promise<void> {
  const client = await createClient(opts);

  if (!client.isConfigured()) {
    console.error('❌ No AI provider configured. Set AI_KEY or OPENAI_API_KEY.');
    process.exit(1);
  }

  const contextBlock = await getGraphContext(prompt);
  const sysPrompt = 'You are AgentX, a helpful coding assistant. Be concise and precise.' 
    + (contextBlock ? `\n\nHere is relevant context from the repository:\n${contextBlock}` : '');

  const messages = [
    { role: 'system' as const, content: sysPrompt },
    { role: 'user' as const, content: prompt },
  ];

  try {
    if (opts.stream) {
      // Streaming mode: print deltas as they arrive
      for await (const chunk of client.stream(messages, opts.provider)) {
        if (chunk.delta) process.stdout.write(chunk.delta);
        if (chunk.done) process.stdout.write('\n');
      }
    } else {
      // Standard mode: wait for full response
      const response = await client.chat(messages, opts.provider);
      console.log(response.content);
    }

    if (opts.showCost) {
      printCost(client.getCostTracker());
    }
  } catch (error: any) {
    console.error(`❌ ${error.message}`);
    process.exit(1);
  }
}
