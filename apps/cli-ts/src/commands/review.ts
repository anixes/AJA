/**
 * agentx review [path]
 * 
 * Diff-aware code review: auto-detects git changes,
 * constructs a review prompt, and returns structured feedback.
 */

import { execSync } from 'child_process';
import { readFileSync, existsSync } from 'fs';
import type { CommandOptions } from './shared.js';
import { createClient, printCost } from './shared.js';

function getGitDiff(cwd: string, targetPath: string): string {
  // Try staged changes first, then unstaged
  let diff = '';
  try {
    diff = execSync(`git diff --cached -- ${targetPath}`, {
      cwd, encoding: 'utf8', timeout: 15_000, maxBuffer: 1024 * 1024,
    }).trim();
  } catch {}

  if (!diff) {
    try {
      diff = execSync(`git diff -- ${targetPath}`, {
        cwd, encoding: 'utf8', timeout: 15_000, maxBuffer: 1024 * 1024,
      }).trim();
    } catch {}
  }

  return diff;
}

export async function reviewCommand(targetPath: string, opts: CommandOptions): Promise<void> {
  const client = await createClient(opts);
  const cwd = process.cwd();

  if (!client.isConfigured()) {
    console.error('❌ No AI provider configured. Set AI_KEY or OPENAI_API_KEY.');
    process.exit(1);
  }

  // Get the diff
  const diff = getGitDiff(cwd, targetPath);

  if (!diff) {
    // If no git diff, try reading the file directly
    if (targetPath !== '.' && existsSync(targetPath)) {
      const content = readFileSync(targetPath, 'utf8');
      console.log('📝 No git diff found. Reviewing file content directly.\n');

      const messages = [
        { role: 'system' as const, content: `You are AgentX, an expert code reviewer. Review the following code for:
1. Bugs and logic errors
2. Security vulnerabilities
3. Performance issues
4. Code style and best practices
5. Missing edge cases

Be specific, cite line numbers, and suggest fixes.` },
        { role: 'user' as const, content: `Review this file (${targetPath}):\n\n\`\`\`\n${content.slice(0, 50_000)}\n\`\`\`` },
      ];

      const response = await client.chat(messages, opts.provider);
      console.log(response.content);
      if (opts.showCost) printCost(client.getCostTracker());
      return;
    }

    console.log('No changes detected. Stage some changes or specify a file to review.');
    return;
  }

  console.log(`📋 Reviewing ${diff.split('\n').length} lines of diff...\n`);

  const messages = [
    { role: 'system' as const, content: `You are AgentX, an expert code reviewer. Review the following git diff for:
1. **Bugs**: Logic errors, off-by-one, null/undefined risks
2. **Security**: Injection, secrets exposure, unsafe patterns
3. **Performance**: Unnecessary allocations, N+1 queries, blocking calls
4. **Style**: Naming, readability, dead code
5. **Missing**: Edge cases, error handling, tests

For each issue, provide:
- Severity: 🔴 Critical / 🟡 Warning / 🔵 Suggestion
- Location: file and line number
- Problem: what's wrong
- Fix: how to fix it

End with a recommendation: ✅ APPROVE, ⚠️ APPROVE WITH COMMENTS, or ❌ REQUEST CHANGES.` },
    { role: 'user' as const, content: `Review this diff:\n\n\`\`\`diff\n${diff.slice(0, 80_000)}\n\`\`\`` },
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
