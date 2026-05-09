/**
 * AgentX Linter Tool
 * 
 * Auto-detects linting setup and runs appropriate linter.
 * Supports: eslint, tsc --noEmit, ruff, pylint, biome
 */

import { execSync } from 'child_process';
import { existsSync } from 'fs';
import path from 'path';
import { z } from 'zod';
import type { ToolDefinition } from '../types/tool.js';

const lintSchema = z.object({
  file: z.string().optional().describe('Specific file or directory to lint'),
  fix: z.boolean().optional().describe('Auto-fix issues where possible'),
});

type Linter = 'eslint' | 'biome' | 'tsc' | 'ruff' | 'pylint' | 'unknown';

function detectLinter(cwd: string): Linter[] {
  const linters: Linter[] = [];

  try {
    const pkgPath = path.join(cwd, 'package.json');
    if (existsSync(pkgPath)) {
      const pkg = JSON.parse(require('fs').readFileSync(pkgPath, 'utf8'));
      const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };

      if (allDeps.eslint || existsSync(path.join(cwd, '.eslintrc.json')) ||
          existsSync(path.join(cwd, '.eslintrc.js')) || existsSync(path.join(cwd, 'eslint.config.js'))) {
        linters.push('eslint');
      }
      if (allDeps['@biomejs/biome'] || existsSync(path.join(cwd, 'biome.json'))) {
        linters.push('biome');
      }
      if (existsSync(path.join(cwd, 'tsconfig.json'))) {
        linters.push('tsc');
      }
    }

    // Python linters
    if (existsSync(path.join(cwd, 'pyproject.toml')) || existsSync(path.join(cwd, 'ruff.toml'))) {
      linters.push('ruff');
    }
  } catch {}

  return linters.length > 0 ? linters : ['unknown'];
}

function buildLintCommand(linter: Linter, file?: string, fix?: boolean): string {
  switch (linter) {
    case 'eslint':
      return `npx eslint ${file || '.'} ${fix ? '--fix' : ''} --format compact`.trim();
    case 'biome':
      return `npx biome check ${file || '.'} ${fix ? '--fix' : ''}`.trim();
    case 'tsc':
      return 'npx tsc --noEmit --pretty';
    case 'ruff':
      return `ruff check ${file || '.'} ${fix ? '--fix' : ''}`.trim();
    case 'pylint':
      return `pylint ${file || '.'}`;
    default:
      return 'npm run lint';
  }
}

export const linterTool: ToolDefinition<typeof lintSchema> = {
  name: 'lint',
  description: 'Run linter on project or specific files. Auto-detects: eslint, biome, tsc, ruff.',
  inputSchema: lintSchema,
  permissionLevel: 'default',
  call: async ({ file, fix }, context) => {
    const linters = detectLinter(context.cwd);
    const results: string[] = [];
    let hasErrors = false;

    for (const linter of linters) {
      const cmd = buildLintCommand(linter, file, fix);
      try {
        const output = execSync(cmd, {
          cwd: context.cwd,
          encoding: 'utf8',
          timeout: 60_000,
          maxBuffer: 2 * 1024 * 1024,
          env: { ...process.env, FORCE_COLOR: '0' },
        }).trim();

        results.push(`── ${linter} ──\n${output || '✓ No issues found.'}`);
      } catch (error: any) {
        hasErrors = true;
        const output = error.stdout || error.stderr || error.message;
        results.push(`── ${linter} (errors) ──\n${output}`);
      }
    }

    return {
      output: results.join('\n\n'),
      summary: hasErrors ? `Lint issues found (${linters.join(', ')})` : `Clean (${linters.join(', ')})`,
      isError: hasErrors,
    };
  },
};
