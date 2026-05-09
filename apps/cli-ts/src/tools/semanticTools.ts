import { execSync } from 'child_process';
import { readFileSync, existsSync } from 'fs';
import path from 'path';
import { z } from 'zod';
import { ToolDefinition, ToolResult } from '../types/tool.js';

const searchSchema = z.object({
  query: z.string().describe('The class, function, or tool name to search for.'),
});

const snippetSchema = z.object({
  path: z.string().describe('The file path.'),
  startLine: z.number().describe('The starting line number.'),
  endLine: z.number().describe('The ending line number.'),
});

/**
 * Runs the local extractor and searches the generated graph.
 */
export const semanticSearchTool: ToolDefinition<typeof searchSchema> = {
  name: 'semantic_search',
  description: 'Search for symbols (classes, functions, tools) across the project without reading files.',
  inputSchema: searchSchema,
  permissionLevel: 'default',
  call: async ({ query }, context) => {
    try {
      // 1. Run the extractor
      execSync('python scripts/local_extractor.py', { cwd: context.cwd });
      
      // 2. Read the result
      const graphPath = path.join(context.cwd, 'graphify-out', 'graph_local.json');
      if (!existsSync(graphPath)) {
        return { output: 'Error: Semantic graph not generated.', isError: true };
      }
      
      const graph = JSON.parse(readFileSync(graphPath, 'utf8'));
      const matches = graph.nodes.filter((node: any) => 
        node.label.toLowerCase().includes(query.toLowerCase()) || 
        node.id.toLowerCase().includes(query.toLowerCase())
      );
      
      if (matches.length === 0) {
        return { output: `No symbols found matching "${query}".` };
      }
      
      const result = matches.map((m: any) => 
        `- [${m.type.toUpperCase()}] ${m.label} in ${m.path}${m.line ? ` (Line ${m.line})` : ''}`
      ).join('\n');
      
      return {
        output: `Found ${matches.length} matches:\n${result}`,
        summary: `Search for "${query}" found ${matches.length} results.`,
        metadata: { query, matchCount: matches.length }
      };
    } catch (error: any) {
      return { output: `Search failed: ${error.message}`, isError: true };
    }
  }
};

/**
 * Reads a specific snippet of code.
 */
export const readSnippetTool: ToolDefinition<typeof snippetSchema> = {
  name: 'read_snippet',
  description: 'Read a specific range of lines from a file.',
  inputSchema: snippetSchema,
  permissionLevel: 'default',
  call: async ({ path: filePath, startLine, endLine }, context) => {
    const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(context.cwd, filePath);
    
    if (!existsSync(absolutePath)) {
      return { output: `Error: File not found at ${filePath}`, isError: true };
    }
    
    try {
      const content = readFileSync(absolutePath, 'utf8');
      const lines = content.split('\n');
      const snippet = lines.slice(startLine - 1, endLine).join('\n');
      
      return {
        output: `--- ${filePath} (Lines ${startLine}-${endLine}) ---\n${snippet}`,
        summary: `Read snippet from ${filePath}`,
        metadata: { path: filePath, startLine, endLine }
      };
    } catch (error: any) {
      return { output: `Error reading snippet: ${error.message}`, isError: true };
    }
  }
};
