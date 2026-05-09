/**
 * AgentX Tool Manager (v2)
 * 
 * Registers all tools and enforces execution mode policies
 * before dispatching tool calls.
 */

import { ToolDefinition, ToolResult } from '../types/tool.js';
import type { ExecutionMode } from './executionModes.js';
import { checkModePermission } from './executionModes.js';

// ── Core Tools ────────────────────────────────────────────────
import { bashTool } from '../tools/bashTool.js';
import { vaultTool } from '../tools/vaultTool.js';
import { fileEditTool, fileReadTool } from '../tools/fileTools.js';
import { semanticSearchTool, readSnippetTool } from '../tools/semanticTools.js';

// ── New Tools ─────────────────────────────────────────────────
import { gitStatusTool, gitDiffTool, gitLogTool, gitBranchTool, gitCommitTool } from '../tools/gitTools.js';
import { testRunnerTool } from '../tools/testRunner.js';
import { linterTool } from '../tools/linterTool.js';
import { grepTool } from '../tools/grepTool.js';
import { webSearchTool } from '../tools/webSearch.js';

/** Maps tool names to the operation type for mode checking */
const TOOL_OPERATION_MAP: Record<string, 'read-file' | 'write-file' | 'bash' | 'git-write' | 'git-commit'> = {
  file_read: 'read-file',
  read_snippet: 'read-file',
  semantic_search: 'read-file',
  file_edit: 'write-file',
  bash: 'bash',
  run_tests: 'bash',
  lint: 'bash',
  grep: 'read-file',
  web_search: 'read-file',
  git_status: 'read-file',
  git_diff: 'read-file',
  git_log: 'read-file',
  git_branch: 'git-write',
  git_commit: 'git-commit',
};

export class ToolManager {
  private tools: Map<string, ToolDefinition<any>> = new Map();
  private mode: ExecutionMode;

  constructor(mode: ExecutionMode = 'ask-before-edit') {
    this.mode = mode;

    // Register all tools
    // Core tools (existing)
    this.registerTool(bashTool);
    this.registerTool(vaultTool);
    this.registerTool(fileEditTool);
    this.registerTool(fileReadTool);
    this.registerTool(semanticSearchTool);
    this.registerTool(readSnippetTool);

    // New tools
    this.registerTool(gitStatusTool);
    this.registerTool(gitDiffTool);
    this.registerTool(gitLogTool);
    this.registerTool(gitBranchTool);
    this.registerTool(gitCommitTool);
    this.registerTool(testRunnerTool);
    this.registerTool(linterTool);
    this.registerTool(grepTool);
    this.registerTool(webSearchTool);
  }

  registerTool(tool: ToolDefinition<any>) {
    this.tools.set(tool.name, tool);
  }

  getTool(name: string): ToolDefinition<any> | undefined {
    return this.tools.get(name);
  }

  listTools(): ToolDefinition<any>[] {
    return Array.from(this.tools.values());
  }

  /**
   * Get tools available in the current mode.
   * (Some tools are hidden in restrictive modes)
   */
  getAvailableTools(): ToolDefinition<any>[] {
    return this.listTools().filter(tool => {
      const operation = TOOL_OPERATION_MAP[tool.name];
      if (!operation) return true;
      const permission = checkModePermission(this.mode, operation);
      return permission !== 'deny';
    });
  }

  setMode(mode: ExecutionMode): void {
    this.mode = mode;
  }

  getMode(): ExecutionMode {
    return this.mode;
  }

  async executeTool(name: string, input: any, context: any): Promise<ToolResult> {
    const tool = this.getTool(name);
    if (!tool) {
      throw new Error(`Tool ${name} not found.`);
    }

    // Inject mode into context
    context.mode = this.mode;

    // Check mode permission before executing
    const operation = TOOL_OPERATION_MAP[name];
    if (operation) {
      const permission = checkModePermission(this.mode, operation, {
        filePath: input.path || input.file,
      });

      if (permission === 'deny') {
        return {
          output: `⛔ Blocked: "${name}" is not allowed in ${this.mode} mode.`,
          isError: true,
        };
      }

      if (permission === 'approval-needed' && !context.approvalGranted) {
        return {
          output: `⚠️ "${name}" requires approval in ${this.mode} mode.`,
          requiresApproval: true,
          approvalCommand: `${name}: ${JSON.stringify(input).slice(0, 200)}`,
        };
      }
    }

    // Validate input and execute
    const validatedInput = tool.inputSchema.parse(input);
    return await tool.call(validatedInput, context);
  }
}
