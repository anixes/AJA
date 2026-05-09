/**
 * AgentX Web Search Tool (Optional)
 * 
 * Lightweight web search via DuckDuckGo Instant Answer API (no API key needed).
 * Can be extended with Brave, SearXNG, or Tavily if keys are available.
 */

import { z } from 'zod';
import type { ToolDefinition } from '../types/tool.js';

const searchSchema = z.object({
  query: z.string().describe('Search query'),
  maxResults: z.number().optional().describe('Max results to return (default: 5)'),
});

async function ddgSearch(query: string, max: number): Promise<string> {
  // DuckDuckGo Instant Answer API (free, no key needed)
  const url = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`;

  const response = await fetch(url, {
    headers: { 'User-Agent': 'AgentX/1.0' },
    signal: AbortSignal.timeout(10_000),
  });

  if (!response.ok) throw new Error(`DuckDuckGo API error: ${response.status}`);
  const data = await response.json() as any;

  const results: string[] = [];

  // Abstract (main answer)
  if (data.Abstract) {
    results.push(`📋 ${data.Abstract}\n   Source: ${data.AbstractURL}`);
  }

  // Related topics
  const topics = (data.RelatedTopics || []).slice(0, max);
  for (const topic of topics) {
    if (topic.Text && topic.FirstURL) {
      results.push(`• ${topic.Text}\n  ${topic.FirstURL}`);
    }
  }

  // Results
  if (data.Results) {
    for (const r of data.Results.slice(0, max)) {
      if (r.Text && r.FirstURL) {
        results.push(`• ${r.Text}\n  ${r.FirstURL}`);
      }
    }
  }

  return results.length > 0
    ? results.join('\n\n')
    : `No instant results for "${query}". Try a more specific query.`;
}

export const webSearchTool: ToolDefinition<typeof searchSchema> = {
  name: 'web_search',
  description: 'Search the web for information. Uses DuckDuckGo (no API key needed).',
  inputSchema: searchSchema,
  permissionLevel: 'default',
  call: async ({ query, maxResults }, _context) => {
    try {
      const output = await ddgSearch(query, maxResults || 5);
      return {
        output,
        summary: `Web search: "${query}"`,
        metadata: { query, engine: 'duckduckgo' },
      };
    } catch (error: any) {
      return { output: `Web search failed: ${error.message}`, isError: true };
    }
  },
};
