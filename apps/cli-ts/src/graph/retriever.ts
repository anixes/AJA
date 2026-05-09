/**
 * AgentX Context Retriever
 * 
 * The brain's intelligence for LLM context injection.
 * Instead of loading the whole repo, it:
 *   1. Searches the graph for relevant symbols
 *   2. Ranks files by relevance to the query
 *   3. Reads only the top files
 *   4. Returns minimal context with estimated token count
 * 
 * This is what makes AgentX the cheapest agent — minimal token usage.
 */

import { readFileSync, existsSync } from 'fs';
import path from 'path';
import { GraphQuery } from './query.js';
import type { RepoGraph, GraphNode, RetrievalResult } from './types.js';

// Rough token estimate: ~4 chars per token for code
const CHARS_PER_TOKEN = 4;

export class ContextRetriever {
  private query: GraphQuery;
  private cwd: string;

  constructor(graph: RepoGraph, cwd: string = process.cwd()) {
    this.query = new GraphQuery(graph);
    this.cwd = cwd;
  }

  /**
   * Retrieve the most relevant context for a prompt.
   * Returns ranked files with content, capped at maxTokens.
   */
  retrieve(prompt: string, maxTokens: number = 8000): RetrievalResult {
    // Extract keywords from the prompt
    const keywords = this.extractKeywords(prompt);

    // Score every file against the keywords
    const fileScores = new Map<string, { score: number; symbols: string[] }>();

    for (const keyword of keywords) {
      // Search the graph for matches
      const matches = this.query.search(keyword, 15);
      for (const match of matches) {
        const existing = fileScores.get(match.filePath) || { score: 0, symbols: [] };
        existing.score += this.scoreMatch(match, keyword);
        if (!existing.symbols.includes(match.name)) {
          existing.symbols.push(match.name);
        }
        fileScores.set(match.filePath, existing);
      }

      // Also get related symbols for better coverage
      const related = this.query.related(keyword, 5);
      for (const rel of related) {
        const existing = fileScores.get(rel.filePath) || { score: 0, symbols: [] };
        existing.score += this.scoreMatch(rel, keyword) * 0.5; // Discount related
        if (!existing.symbols.includes(rel.name)) {
          existing.symbols.push(rel.name);
        }
        fileScores.set(rel.filePath, existing);
      }
    }

    // Rank files by score, read content, cap at token budget
    const ranked = [...fileScores.entries()]
      .sort((a, b) => b[1].score - a[1].score);

    const result: RetrievalResult = { files: [], estimatedTokens: 0 };
    let remainingTokens = maxTokens;

    for (const [filePath, { score, symbols }] of ranked) {
      if (remainingTokens <= 0) break;

      const content = this.readFile(filePath);
      if (!content) continue;

      const fileTokens = Math.ceil(content.length / CHARS_PER_TOKEN);

      if (fileTokens <= remainingTokens) {
        // Full file fits
        result.files.push({
          path: filePath,
          relevance: Math.min(1, score / 100),
          content,
          symbols,
        });
        remainingTokens -= fileTokens;
        result.estimatedTokens += fileTokens;
      } else if (remainingTokens > 200) {
        // Partial: extract only the relevant sections
        const partial = this.extractRelevantSections(content, symbols, remainingTokens * CHARS_PER_TOKEN);
        const partialTokens = Math.ceil(partial.length / CHARS_PER_TOKEN);
        result.files.push({
          path: filePath,
          relevance: Math.min(1, score / 100) * 0.8, // Discount partial
          content: partial,
          symbols,
        });
        remainingTokens -= partialTokens;
        result.estimatedTokens += partialTokens;
      }
    }

    return result;
  }

  /**
   * Build an LLM-ready context block from retrieval results.
   */
  formatForLLM(result: RetrievalResult): string {
    if (result.files.length === 0) return '';

    const sections = result.files.map(f => {
      const ext = path.extname(f.path).slice(1) || 'txt';
      return `── ${f.path} (relevance: ${(f.relevance * 100).toFixed(0)}%) ──
\`\`\`${ext}
${f.content}
\`\`\``;
    });

    return `<repo_context files="${result.files.length}" tokens="~${result.estimatedTokens}">
${sections.join('\n\n')}
</repo_context>`;
  }

  /**
   * Quick relevance check: is this prompt about code in this repo?
   */
  hasRelevantContext(prompt: string): boolean {
    const keywords = this.extractKeywords(prompt);
    for (const kw of keywords.slice(0, 5)) {
      if (this.query.search(kw, 1).length > 0) return true;
    }
    return false;
  }

  // ── Expose the underlying query engine ──────────────────────

  getQueryEngine(): GraphQuery {
    return this.query;
  }

  // ── Private ─────────────────────────────────────────────────

  private extractKeywords(prompt: string): string[] {
    // Extract meaningful identifiers from the prompt
    const words = prompt
      .replace(/[^\w\s./\\-]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 2)
      .filter(w => !STOP_WORDS.has(w.toLowerCase()));

    // Also extract camelCase/PascalCase parts
    const camelParts: string[] = [];
    for (const word of words) {
      const parts = word.replace(/([a-z])([A-Z])/g, '$1 $2').split(' ');
      if (parts.length > 1) camelParts.push(...parts.filter(p => p.length > 2));
    }

    // Also extract file paths
    const pathMatches = prompt.match(/[\w./\\-]+\.\w+/g) || [];

    return [...new Set([...words, ...camelParts, ...pathMatches])];
  }

  private scoreMatch(node: GraphNode, keyword: string): number {
    const name = node.name.toLowerCase();
    const kw = keyword.toLowerCase();
    let score = 0;

    // Name matching
    if (name === kw) score += 50;
    else if (name.startsWith(kw)) score += 30;
    else if (name.includes(kw)) score += 15;
    else if (node.filePath.includes(kw)) score += 10;

    // Boost important node types
    const typeBoost: Record<string, number> = {
      file: 5, class: 8, function: 6, interface: 4,
      route: 10, test: 3, schema: 7, component: 6,
    };
    score += typeBoost[node.kind] || 0;

    // Boost exported symbols
    if (node.exported) score += 3;

    return score;
  }

  private readFile(filePath: string): string | null {
    const fullPath = path.join(this.cwd, filePath);
    if (!existsSync(fullPath)) return null;
    try {
      return readFileSync(fullPath, 'utf8');
    } catch {
      return null;
    }
  }

  /**
   * Extract only the sections of a file that contain the relevant symbols.
   * Returns a trimmed version with context around matching lines.
   */
  private extractRelevantSections(content: string, symbols: string[], maxChars: number): string {
    const lines = content.split('\n');
    const relevantLines = new Set<number>();

    // Reduce context window to minimize token usage
    const CONTEXT_BEFORE = 2;
    const CONTEXT_AFTER = 8;

    // Find lines containing any symbol
    for (let i = 0; i < lines.length; i++) {
      for (const sym of symbols) {
        if (lines[i].includes(sym)) {
          // Include context window around match
          for (let j = Math.max(0, i - CONTEXT_BEFORE); j <= Math.min(lines.length - 1, i + CONTEXT_AFTER); j++) {
            relevantLines.add(j);
          }
        }
      }
    }

    if (relevantLines.size === 0) {
      // Fallback: return the first few lines that fit in maxChars
      let currentLength = 0;
      const fallbackLines: string[] = [];
      for (const line of lines) {
        if (currentLength + line.length > maxChars) break;
        fallbackLines.push(line);
        currentLength += line.length + 1; // +1 for newline
      }
      return fallbackLines.join('\n');
    }

    // Build output with ellipsis for gaps
    const sorted = [...relevantLines].sort((a, b) => a - b);
    const sections: string[] = [];
    let lastLine = -1;
    let currentLength = 0;

    for (const lineNum of sorted) {
      if (lastLine !== -1 && lineNum > lastLine + 1) {
        const ellipsis = `... (lines ${lastLine + 2}-${lineNum} omitted)`;
        sections.push(ellipsis);
        currentLength += ellipsis.length + 1;
      }
      sections.push(lines[lineNum]);
      currentLength += lines[lineNum].length + 1;
      lastLine = lineNum;
      
      if (currentLength >= maxChars) break;
    }

    return sections.join('\n').slice(0, maxChars);
  }
}

const STOP_WORDS = new Set([
  'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was',
  'will', 'can', 'has', 'have', 'had', 'been', 'not', 'but', 'they',
  'what', 'when', 'where', 'how', 'which', 'who', 'why', 'all',
  'each', 'any', 'some', 'into', 'also', 'than', 'then', 'its',
  'use', 'using', 'used', 'make', 'like', 'just', 'should', 'could',
  'would', 'about', 'there', 'their', 'does', 'show', 'want', 'need',
  'file', 'code', 'function', 'class', 'import', 'export', 'module',
]);
