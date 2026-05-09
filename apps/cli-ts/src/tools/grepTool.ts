/**
 * AgentX Grep/Search Tool
 * 
 * Fast project-wide text search.
 * Uses ripgrep (rg) if available, falls back to built-in recursive grep.
 */

import { execSync } from 'child_process';
import { readdirSync, readFileSync, statSync } from 'fs';
import path from 'path';
import { z } from 'zod';
import type { ToolDefinition } from '../types/tool.js';

const grepSchema = z.object({
  query: z.string().describe('Search pattern (string or regex)'),
  path: z.string().optional().describe('File or directory to search in (default: project root)'),
  regex: z.boolean().optional().describe('Treat query as regex pattern'),
  caseSensitive: z.boolean().optional().describe('Case-sensitive search (default: false)'),
  context: z.number().optional().describe('Lines of context around each match (default: 2)'),
  filePattern: z.string().optional().describe('File glob pattern, e.g. "*.ts" or "*.py"'),
  maxResults: z.number().optional().describe('Maximum number of results (default: 50)'),
});

const IGNORE_DIRS = new Set([
  'node_modules', '.git', 'dist', 'build', '.next', '__pycache__',
  'coverage', '.cache', '.turbo', 'vendor', '.venv', 'venv',
]);

function hasRipgrep(): boolean {
  try {
    execSync('rg --version', { encoding: 'utf8', timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}

function ripgrepSearch(query: string, cwd: string, opts: {
  searchPath?: string; regex?: boolean; caseSensitive?: boolean;
  context?: number; filePattern?: string; maxResults?: number;
}): string {
  const parts = ['rg', '--no-heading', '--line-number', '--color=never'];

  if (!opts.caseSensitive) parts.push('-i');
  if (!opts.regex) parts.push('-F'); // fixed string
  parts.push(`-m ${opts.maxResults || 50}`);
  if (opts.context) parts.push(`-C ${opts.context}`);
  if (opts.filePattern) parts.push(`-g "${opts.filePattern}"`);

  parts.push(`"${query.replace(/"/g, '\\"')}"`);
  if (opts.searchPath) parts.push(opts.searchPath);

  return execSync(parts.join(' '), {
    cwd,
    encoding: 'utf8',
    timeout: 30_000,
    maxBuffer: 2 * 1024 * 1024,
  }).trim();
}

function builtinSearch(query: string, cwd: string, opts: {
  searchPath?: string; caseSensitive?: boolean; maxResults?: number; filePattern?: string;
}): string {
  const results: string[] = [];
  const max = opts.maxResults || 50;
  const searchRoot = opts.searchPath ? path.resolve(cwd, opts.searchPath) : cwd;
  const matcher = opts.caseSensitive ? query : query.toLowerCase();
  const globRe = opts.filePattern ? new RegExp(opts.filePattern.replace(/\*/g, '.*').replace(/\?/g, '.')) : null;

  function walk(dir: string): void {
    if (results.length >= max) return;
    try {
      for (const entry of readdirSync(dir)) {
        if (results.length >= max) return;
        if (IGNORE_DIRS.has(entry)) continue;
        const full = path.join(dir, entry);
        const st = statSync(full);
        if (st.isDirectory()) { walk(full); continue; }
        if (!st.isFile() || st.size > 512_000) continue; // Skip large files
        if (globRe && !globRe.test(entry)) continue;
        try {
          const content = readFileSync(full, 'utf8');
          const lines = content.split('\n');
          for (let i = 0; i < lines.length; i++) {
            if (results.length >= max) return;
            const line = opts.caseSensitive ? lines[i] : lines[i].toLowerCase();
            if (line.includes(matcher)) {
              const rel = path.relative(cwd, full);
              results.push(`${rel}:${i + 1}:${lines[i].trim()}`);
            }
          }
        } catch { /* binary file or read error */ }
      }
    } catch {}
  }

  walk(searchRoot);
  return results.join('\n');
}

export const grepTool: ToolDefinition<typeof grepSchema> = {
  name: 'grep',
  description: 'Search for text/patterns across project files. Uses ripgrep if available.',
  inputSchema: grepSchema,
  permissionLevel: 'default',
  call: async ({ query, path: searchPath, regex, caseSensitive, context: ctx, filePattern, maxResults }, ctxObj) => {
    try {
      let output: string;

      if (hasRipgrep()) {
        output = ripgrepSearch(query, ctxObj.cwd, { searchPath, regex, caseSensitive, context: ctx || 2, filePattern, maxResults });
      } else {
        output = builtinSearch(query, ctxObj.cwd, { searchPath, caseSensitive, maxResults, filePattern });
      }

      if (!output) {
        return { output: `No matches found for "${query}".` };
      }

      const matchCount = output.split('\n').filter(l => l.trim()).length;
      return {
        output,
        summary: `Found ${matchCount} matches for "${query}"`,
        metadata: { query, matchCount, engine: hasRipgrep() ? 'ripgrep' : 'builtin' },
      };
    } catch (error: any) {
      // rg exits 1 when no matches found
      if (error.status === 1) {
        return { output: `No matches found for "${query}".` };
      }
      return { output: `Search failed: ${error.message}`, isError: true };
    }
  },
};
