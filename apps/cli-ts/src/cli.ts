#!/usr/bin/env node
/**
 * Agent CLI Entry Point
 * 
 * Commands:
 *   agent ask "prompt"          → single-shot query
 *   agent review [path]         → diff-aware code review
 *   agent code "task"           → agentic coding loop
 *   agent fix <file>            → error diagnosis + fix
 *   agent explain <target>      → semantic analysis
 *   agent map [path]            → index repo & show graph summary
 *   agent trace <query>         → trace symbol connections
 *   agent impact <file|symbol>  → blast radius analysis
 *   agent (no args)             → interactive TUI mode
 */

import { Command } from 'commander';
import { askCommand } from './commands/ask.js';
import { reviewCommand } from './commands/review.js';
import { codeCommand } from './commands/code.js';
import { fixCommand } from './commands/fix.js';
import { explainCommand } from './commands/explain.js';
import { mapCommand } from './commands/map.js';
import { traceCommand } from './commands/trace.js';
import { impactCommand } from './commands/impact.js';
import type { ExecutionMode } from './engine/executionModes.js';

const program = new Command();

program
  .name('agent')
  .description('The cheapest serious coding agent — local-first, repo-aware, safely autonomous.')
  .version('1.0.0');

// ── Global Options ────────────────────────────────────────────

program
  .option('--mode <mode>', 'Execution mode: read-only, suggest-only, ask-before-edit, auto-edit-safe, autonomous-branch', 'ask-before-edit')
  .option('--model <model>', 'Model override (e.g. gpt-4o, claude-sonnet-4-20250514, gemini-2.5-flash)')
  .option('--provider <name>', 'Provider override (e.g. openai, anthropic, gemini, llama_cpp)')
  .option('--stream', 'Enable streaming output')
  .option('--cost', 'Show cost summary after execution')
  .option('--verbose', 'Verbose output');

// ── Phase 1: Core Commands ───────────────────────────────────

program
  .command('ask')
  .argument('<prompt...>', 'Your question or prompt')
  .description('Single-shot query — ask a question, get an answer, exit.')
  .action(async (promptParts: string[]) => {
    const opts = program.opts();
    await askCommand(promptParts.join(' '), {
      mode: opts.mode as ExecutionMode,
      model: opts.model,
      provider: opts.provider,
      stream: opts.stream,
      showCost: opts.cost,
    });
  });

program
  .command('review')
  .argument('[path]', 'File or directory to review', '.')
  .description('Review code changes — auto-detects git diff.')
  .action(async (targetPath: string) => {
    const opts = program.opts();
    await reviewCommand(targetPath, {
      mode: opts.mode as ExecutionMode,
      model: opts.model,
      provider: opts.provider,
      stream: opts.stream,
      showCost: opts.cost,
    });
  });

program
  .command('code')
  .argument('<task...>', 'Coding task description')
  .description('Agentic coding loop — plans, writes, and tests code.')
  .action(async (taskParts: string[]) => {
    const opts = program.opts();
    await codeCommand(taskParts.join(' '), {
      mode: opts.mode as ExecutionMode,
      model: opts.model,
      provider: opts.provider,
      stream: opts.stream,
      showCost: opts.cost,
    });
  });

program
  .command('fix')
  .argument('<file>', 'File to diagnose and fix')
  .description('Diagnose errors in a file and propose/apply fixes.')
  .action(async (file: string) => {
    const opts = program.opts();
    await fixCommand(file, {
      mode: opts.mode as ExecutionMode,
      model: opts.model,
      provider: opts.provider,
      stream: opts.stream,
      showCost: opts.cost,
    });
  });

program
  .command('explain')
  .argument('<target>', 'Module, function, or file to explain')
  .description('Explain a module or function using semantic search.')
  .action(async (target: string) => {
    const opts = program.opts();
    await explainCommand(target, {
      mode: opts.mode as ExecutionMode,
      model: opts.model,
      provider: opts.provider,
      showCost: opts.cost,
    });
  });

// ── Phase 2: Graph / Repo Brain Commands ─────────────────────

program
  .command('map')
  .argument('[path]', 'Directory to index', '.')
  .description('Index repo into knowledge graph — shows structure, symbols, and connections.')
  .option('--full', 'Force full re-index (skip incremental)')
  .option('--json', 'Output raw graph stats as JSON')
  .action(async (targetPath: string, cmdOpts: Record<string, unknown>) => {
    const opts = program.opts();
    await mapCommand(targetPath, {
      full: !!cmdOpts.full,
      json: !!cmdOpts.json,
      verbose: opts.verbose,
    });
  });

program
  .command('trace')
  .argument('<query>', 'Symbol name or "from -> to" path query')
  .description('Trace connections between symbols in the codebase graph.')
  .option('--depth <n>', 'Max trace depth', '5')
  .action(async (query: string, cmdOpts: Record<string, unknown>) => {
    const opts = program.opts();
    await traceCommand(query, {
      depth: Number(cmdOpts.depth) || 5,
      verbose: opts.verbose,
    });
  });

program
  .command('impact')
  .argument('<target>', 'File or symbol to analyze')
  .description('Blast radius analysis — what breaks if you change this?')
  .option('--depth <n>', 'Transitive dependency depth', '3')
  .action(async (target: string, cmdOpts: Record<string, unknown>) => {
    const opts = program.opts();
    await impactCommand(target, {
      depth: Number(cmdOpts.depth) || 3,
      verbose: opts.verbose,
    });
  });

// ── Default: Interactive TUI ──────────────────────────────────

program
  .command('chat', { isDefault: true })
  .description('Launch interactive TUI chat mode (default).')
  .action(async () => {
    // Dynamic import to run the interactive TUI
    await import('./main.js');
  });

program
  .command('completion [shell]')
  .description('Generate shell completion script (bash or zsh)')
  .action(async (shell) => {
    const { getCompletionScript } = await import('./commands/completion.js');
    const targetShell = shell || (process.env.SHELL?.includes('zsh') ? 'zsh' : 'bash');
    console.log(getCompletionScript(targetShell));
    if (!shell) {
      console.error(`\n# To enable, run: eval "$(agent completion)"`);
    }
  });

// ── Parse & Run ───────────────────────────────────────────────

program.parse();

