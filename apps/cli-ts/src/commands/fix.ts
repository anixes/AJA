/**
 * agentx fix <file>
 * 
 * Reads a file, diagnoses issues (type errors, bugs, lint violations),
 * and proposes/applies fixes respecting the execution mode.
 */

import { readFileSync, existsSync } from 'fs';
import { execSync } from 'child_process';
import path from 'path';
import type { CommandOptions } from './shared.js';
import { createClient, printCost, getGraphContext } from './shared.js';

function getFileErrors(filePath: string, cwd: string): string {
  const errors: string[] = [];
  const ext = path.extname(filePath);

  // TypeScript errors
  if (['.ts', '.tsx'].includes(ext)) {
    try {
      execSync(`npx tsc --noEmit --pretty ${filePath}`, {
        cwd, encoding: 'utf8', timeout: 30_000,
      });
    } catch (e: any) {
      errors.push(`TypeScript Errors:\n${e.stdout || e.stderr || e.message}`);
    }
  }

  // ESLint errors
  try {
    execSync(`npx eslint ${filePath} --format compact`, {
      cwd, encoding: 'utf8', timeout: 15_000,
    });
  } catch (e: any) {
    if (e.stdout) errors.push(`ESLint Errors:\n${e.stdout}`);
  }

  return errors.join('\n\n') || 'No automatic errors detected.';
}

export async function fixCommand(file: string, opts: CommandOptions): Promise<void> {
  const client = await createClient(opts);
  const cwd = process.cwd();
  const filePath = path.resolve(cwd, file);

  if (!client.isConfigured()) {
    console.error('❌ No AI provider configured. Set AI_KEY or OPENAI_API_KEY.');
    process.exit(1);
  }

  if (!existsSync(filePath)) {
    console.error(`❌ File not found: ${file}`);
    process.exit(1);
  }

  console.log(`🔍 Analyzing ${file}...\n`);

  const content = readFileSync(filePath, 'utf8');
  const errors = getFileErrors(file, cwd);

  // Read any piped error input (e.g., from stderr of a previous command)
  let stdinErrors = '';
  if (!process.stdin.isTTY) {
    try {
      stdinErrors = require('fs').readFileSync(0, 'utf8');
    } catch {}
  }

  const diagnostics = [
    errors,
    stdinErrors ? `\nStdin Errors:\n${stdinErrors}` : '',
  ].filter(Boolean).join('\n');

  const contextBlock = await getGraphContext(`${file} ${diagnostics}`);
  if (contextBlock) {
    console.log(`🧠 Injected ${contextBlock.split('\\n').length} lines of graph context.`);
  }

  const modeInstruction = opts.mode === 'suggest-only' || opts.mode === 'read-only'
    ? 'Show the fix as a diff. Do NOT output the full corrected file.'
    : 'Show the fix as a diff and also output the corrected code in a fenced code block.';

  const messages = [
    { role: 'system' as const, content: `You are AgentX, an expert debugger and code fixer.

Given a file and its error diagnostics, you must:
1. Identify the root cause of each error
2. Explain why the error occurs
3. Provide the minimal fix

${modeInstruction}
${contextBlock ? `\nRepository Context:\n${contextBlock}\n` : ''}
Be precise. Only change what's necessary.` },
    { role: 'user' as const, content: `Fix this file: ${file}

File content:
\`\`\`${path.extname(file).slice(1)}
${content.slice(0, 50_000)}
\`\`\`

Diagnostics:
${diagnostics}` },
  ];

  try {
    if (opts.stream) {
      for await (const chunk of client.stream(messages, opts.provider)) {
        if (chunk.delta) process.stdout.write(chunk.delta);
        if (chunk.done) process.stdout.write('\n');
      }
    } else {
      const response = await client.chat(messages, opts.provider);
      console.log(response.content);
    }

    if (opts.showCost) printCost(client.getCostTracker());
  } catch (error: any) {
    console.error(`❌ ${error.message}`);
    process.exit(1);
  }
}
