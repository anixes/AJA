/**
 * AgentX Test Runner Tool
 * 
 * Auto-detects test framework and runs tests.
 * Supports: jest, vitest, mocha, pytest, bun test
 */

import { execSync } from 'child_process';
import { existsSync } from 'fs';
import path from 'path';
import { z } from 'zod';
import type { ToolDefinition } from '../types/tool.js';

const testSchema = z.object({
  file: z.string().optional().describe('Specific test file or pattern to run'),
  watch: z.boolean().optional().describe('Run in watch mode (if supported)'),
  grep: z.string().optional().describe('Filter tests by name pattern'),
  verbose: z.boolean().optional().describe('Verbose output'),
});

type Framework = 'jest' | 'vitest' | 'mocha' | 'pytest' | 'bun' | 'unknown';

function detectFramework(cwd: string): Framework {
  try {
    const pkgPath = path.join(cwd, 'package.json');
    if (existsSync(pkgPath)) {
      const pkg = JSON.parse(require('fs').readFileSync(pkgPath, 'utf8'));
      const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };

      if (allDeps.vitest) return 'vitest';
      if (allDeps.jest || allDeps['@jest/core'] || pkg.jest) return 'jest';
      if (allDeps.mocha) return 'mocha';

      // Check scripts for bun test
      if (pkg.scripts?.test?.includes('bun test')) return 'bun';
    }

    // Python project
    if (existsSync(path.join(cwd, 'pytest.ini')) ||
        existsSync(path.join(cwd, 'setup.cfg')) ||
        existsSync(path.join(cwd, 'pyproject.toml'))) {
      return 'pytest';
    }
  } catch {}
  return 'unknown';
}

function buildCommand(framework: Framework, opts: { file?: string; watch?: boolean; grep?: string; verbose?: boolean }): string {
  const parts: string[] = [];

  switch (framework) {
    case 'vitest':
      parts.push('npx vitest run');
      if (opts.watch) parts[0] = 'npx vitest';
      if (opts.file) parts.push(opts.file);
      if (opts.grep) parts.push(`--grep "${opts.grep}"`);
      if (opts.verbose) parts.push('--reporter=verbose');
      break;

    case 'jest':
      parts.push('npx jest');
      if (opts.file) parts.push(opts.file);
      if (opts.grep) parts.push(`-t "${opts.grep}"`);
      if (opts.verbose) parts.push('--verbose');
      if (!opts.watch) parts.push('--no-cache');
      break;

    case 'mocha':
      parts.push('npx mocha');
      if (opts.file) parts.push(opts.file);
      if (opts.grep) parts.push(`--grep "${opts.grep}"`);
      break;

    case 'pytest':
      parts.push('python -m pytest');
      if (opts.file) parts.push(opts.file);
      if (opts.grep) parts.push(`-k "${opts.grep}"`);
      if (opts.verbose) parts.push('-v');
      break;

    case 'bun':
      parts.push('bun test');
      if (opts.file) parts.push(opts.file);
      if (opts.grep) parts.push(`--grep "${opts.grep}"`);
      break;

    default:
      parts.push('npm test');
      break;
  }

  return parts.join(' ');
}

export const testRunnerTool: ToolDefinition<typeof testSchema> = {
  name: 'run_tests',
  description: 'Run project tests. Auto-detects framework (jest, vitest, mocha, pytest, bun test).',
  inputSchema: testSchema,
  permissionLevel: 'default',
  call: async ({ file, watch, grep, verbose }, context) => {
    const framework = detectFramework(context.cwd);
    const cmd = buildCommand(framework, { file, watch, grep, verbose });

    try {
      const output = execSync(cmd, {
        cwd: context.cwd,
        encoding: 'utf8',
        timeout: 120_000, // 2 min timeout for tests
        maxBuffer: 2 * 1024 * 1024,
        env: { ...process.env, FORCE_COLOR: '0', CI: '1' },
      }).trim();

      return {
        output: `[${framework}] ${cmd}\n\n${output}`,
        summary: `Tests completed (${framework})`,
        metadata: { framework, command: cmd },
      };
    } catch (error: any) {
      // Test failures exit non-zero but we still want the output
      const output = error.stdout || error.stderr || error.message;
      return {
        output: `[${framework}] ${cmd}\n\n${output}`,
        summary: `Tests failed (${framework})`,
        metadata: { framework, command: cmd, failed: true },
        isError: true,
      };
    }
  },
};
