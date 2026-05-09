/**
 * AgentX Git Tools
 * 
 * Provides git operations as LLM-callable tools.
 * Write operations (commit, branch create, push) respect execution modes.
 */

import { execSync } from 'child_process';
import { z } from 'zod';
import type { ToolDefinition } from '../types/tool.js';

// ── Git Status ────────────────────────────────────────────────

const statusSchema = z.object({
  short: z.boolean().optional().describe('Use short format output'),
});

export const gitStatusTool: ToolDefinition<typeof statusSchema> = {
  name: 'git_status',
  description: 'Show the working tree status — modified, staged, and untracked files.',
  inputSchema: statusSchema,
  permissionLevel: 'default',
  call: async ({ short }, context) => {
    try {
      const flags = short ? '-s' : '';
      const output = execSync(`git status ${flags}`, {
        cwd: context.cwd,
        encoding: 'utf8',
        timeout: 10_000,
      }).trim();
      return { output: output || 'Working tree clean.' };
    } catch (error: any) {
      return { output: `git status failed: ${error.message}`, isError: true };
    }
  },
};

// ── Git Diff ──────────────────────────────────────────────────

const diffSchema = z.object({
  staged: z.boolean().optional().describe('Show staged changes (--cached)'),
  file: z.string().optional().describe('Specific file to diff'),
  ref: z.string().optional().describe('Compare against a ref (branch, commit, tag)'),
  stat: z.boolean().optional().describe('Show diffstat only'),
});

export const gitDiffTool: ToolDefinition<typeof diffSchema> = {
  name: 'git_diff',
  description: 'Show changes between working tree, index, or commits.',
  inputSchema: diffSchema,
  permissionLevel: 'default',
  call: async ({ staged, file, ref, stat }, context) => {
    try {
      const parts = ['git', 'diff'];
      if (staged) parts.push('--cached');
      if (stat) parts.push('--stat');
      if (ref) parts.push(ref);
      if (file) parts.push('--', file);

      const output = execSync(parts.join(' '), {
        cwd: context.cwd,
        encoding: 'utf8',
        timeout: 15_000,
        maxBuffer: 1024 * 1024,
      }).trim();

      return { output: output || 'No differences found.' };
    } catch (error: any) {
      return { output: `git diff failed: ${error.message}`, isError: true };
    }
  },
};

// ── Git Log ───────────────────────────────────────────────────

const logSchema = z.object({
  count: z.number().optional().describe('Number of commits to show (default: 10)'),
  oneline: z.boolean().optional().describe('Use one-line format'),
  file: z.string().optional().describe('Show history for a specific file'),
});

export const gitLogTool: ToolDefinition<typeof logSchema> = {
  name: 'git_log',
  description: 'Show recent commit history.',
  inputSchema: logSchema,
  permissionLevel: 'default',
  call: async ({ count, oneline, file }, context) => {
    try {
      const n = count || 10;
      const format = oneline ? '--oneline' : '--pretty=format:%h %an %ar %s';
      const parts = ['git', 'log', `-${n}`, format];
      if (file) parts.push('--', file);

      const output = execSync(parts.join(' '), {
        cwd: context.cwd,
        encoding: 'utf8',
        timeout: 10_000,
      }).trim();

      return { output: output || 'No commits found.' };
    } catch (error: any) {
      return { output: `git log failed: ${error.message}`, isError: true };
    }
  },
};

// ── Git Branch ────────────────────────────────────────────────

const branchSchema = z.object({
  action: z.enum(['list', 'create', 'switch']).describe('Action: list, create, or switch branches'),
  name: z.string().optional().describe('Branch name (for create/switch)'),
});

export const gitBranchTool: ToolDefinition<typeof branchSchema> = {
  name: 'git_branch',
  description: 'List, create, or switch git branches.',
  inputSchema: branchSchema,
  permissionLevel: 'high', // Write operations need approval
  call: async ({ action, name }, context) => {
    try {
      if (action === 'list') {
        const output = execSync('git branch -a', {
          cwd: context.cwd, encoding: 'utf8', timeout: 10_000,
        }).trim();
        return { output: output || 'No branches found.' };
      }

      if (!name) return { output: 'Error: Branch name required for create/switch', isError: true };

      if (action === 'create') {
        execSync(`git checkout -b ${name}`, { cwd: context.cwd, encoding: 'utf8', timeout: 10_000 });
        return { output: `Created and switched to branch: ${name}` };
      }

      if (action === 'switch') {
        execSync(`git checkout ${name}`, { cwd: context.cwd, encoding: 'utf8', timeout: 10_000 });
        return { output: `Switched to branch: ${name}` };
      }

      return { output: 'Invalid action', isError: true };
    } catch (error: any) {
      return { output: `git branch failed: ${error.message}`, isError: true };
    }
  },
};

// ── Git Commit ────────────────────────────────────────────────

const commitSchema = z.object({
  message: z.string().describe('Commit message'),
  all: z.boolean().optional().describe('Stage all modified files before committing (-a)'),
  files: z.array(z.string()).optional().describe('Specific files to stage and commit'),
});

export const gitCommitTool: ToolDefinition<typeof commitSchema> = {
  name: 'git_commit',
  description: 'Stage and commit changes. Requires approval in most execution modes.',
  inputSchema: commitSchema,
  permissionLevel: 'high',
  call: async ({ message, all, files }, context) => {
    try {
      // Stage files
      if (files && files.length > 0) {
        execSync(`git add ${files.join(' ')}`, { cwd: context.cwd, encoding: 'utf8', timeout: 10_000 });
      } else if (all) {
        execSync('git add -A', { cwd: context.cwd, encoding: 'utf8', timeout: 10_000 });
      }

      // Commit
      const safeMsg = message.replace(/"/g, '\\"');
      const output = execSync(`git commit -m "${safeMsg}"`, {
        cwd: context.cwd, encoding: 'utf8', timeout: 10_000,
      }).trim();

      return { output, summary: `Committed: ${message}` };
    } catch (error: any) {
      return { output: `git commit failed: ${error.message}`, isError: true };
    }
  },
};

/** All git tools for registration */
export const gitTools = [gitStatusTool, gitDiffTool, gitLogTool, gitBranchTool, gitCommitTool];
