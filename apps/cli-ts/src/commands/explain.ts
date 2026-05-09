/**
 * agentx explain <target>
 * 
 * Semantic analysis: uses grep/search to find the target module or function,
 * reads relevant source code, and generates an architectural explanation.
 */

import { execSync } from 'child_process';
import { readFileSync, existsSync } from 'fs';
import path from 'path';
import type { CommandOptions } from './shared.js';
import { createClient, printCost } from './shared.js';

function findTarget(target: string, cwd: string): Array<{ file: string; line: number; content: string }> {
  const results: Array<{ file: string; line: number; content: string }> = [];

  try {
    // Try ripgrep first
    const output = execSync(
      `rg -n --no-heading -m 20 "${target}" --type ts --type js --type py`,
      { cwd, encoding: 'utf8', timeout: 10_000 }
    ).trim();

    for (const line of output.split('\n').filter(Boolean)) {
      const match = line.match(/^(.+?):(\d+):(.*)$/);
      if (match) {
        results.push({ file: match[1], line: parseInt(match[2]), content: match[3].trim() });
      }
    }
  } catch {
    // Fallback to git grep
    try {
      const output = execSync(
        `git grep -n "${target}" -- "*.ts" "*.js" "*.tsx" "*.py"`,
        { cwd, encoding: 'utf8', timeout: 10_000 }
      ).trim();

      for (const line of output.split('\n').filter(Boolean)) {
        const match = line.match(/^(.+?):(\d+):(.*)$/);
        if (match) {
          results.push({ file: match[1], line: parseInt(match[2]), content: match[3].trim() });
        }
      }
    } catch {}
  }

  return results.slice(0, 10);
}

function readContextAround(filePath: string, line: number, cwd: string, contextLines: number = 30): string {
  const fullPath = path.resolve(cwd, filePath);
  if (!existsSync(fullPath)) return '';

  const content = readFileSync(fullPath, 'utf8');
  const lines = content.split('\n');
  const start = Math.max(0, line - contextLines);
  const end = Math.min(lines.length, line + contextLines);

  return lines.slice(start, end).map((l, i) => `${start + i + 1}: ${l}`).join('\n');
}

export async function explainCommand(target: string, opts: CommandOptions): Promise<void> {
  const client = await createClient(opts);
  const cwd = process.cwd();

  if (!client.isConfigured()) {
    console.error('❌ No AI provider configured. Set AI_KEY or OPENAI_API_KEY.');
    process.exit(1);
  }

  console.log(`🔍 Searching for "${target}"...\n`);

  // Find the target in the codebase
  const matches = findTarget(target, cwd);

  if (matches.length === 0) {
    // Check if it's a file path
    if (existsSync(path.resolve(cwd, target))) {
      const content = readFileSync(path.resolve(cwd, target), 'utf8');
      matches.push({ file: target, line: 1, content: content.split('\n')[0] });
    } else {
      console.log(`No results found for "${target}". Try a different search term.`);
      return;
    }
  }

  console.log(`Found ${matches.length} references:\n`);
  matches.forEach(m => console.log(`  ${m.file}:${m.line} — ${m.content.slice(0, 80)}`));
  console.log();

  // Read source context from the most relevant matches
  const sourceContexts = matches.slice(0, 3).map(m => {
    const ctx = readContextAround(m.file, m.line, cwd);
    return `── ${m.file} (around line ${m.line}) ──\n${ctx}`;
  }).join('\n\n');

  const messages = [
    { role: 'system' as const, content: `You are AgentX, a senior software architect.

Explain the given code module/function/concept clearly:
1. **What it does** — purpose and responsibility
2. **How it works** — key logic and flow
3. **Dependencies** — what it uses and what uses it
4. **Design patterns** — any patterns or architectures used
5. **Key considerations** — gotchas, edge cases, performance notes

Be educational but concise. Use diagrams (ASCII) if helpful.` },
    { role: 'user' as const, content: `Explain "${target}" based on this source code:\n\n${sourceContexts.slice(0, 50_000)}` },
  ];

  try {
    const response = await client.chat(messages, opts.provider);
    console.log(response.content);
    if (opts.showCost) printCost(client.getCostTracker());
  } catch (error: any) {
    console.error(`❌ ${error.message}`);
    process.exit(1);
  }
}
