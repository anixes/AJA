import { CommandContext, CommandDefinition, CommandResult } from '../types/command.js';
import { ToolManager } from '../engine/ToolManager.js';
import type { CostMode } from '../engine/CostMode.js';

interface CostCommandHooks {
  getCostMode: () => CostMode;
  setCostMode: (mode: CostMode) => void;
  getCostStatus: () => string;
  getCostSnapshot: () => {
    totalTokens: number;
    totalCost: number;
    savedPercentage: number;
    savedAmount: number;
    providerBreakdown: Array<{ provider: string; percentage: number; cost: number }>;
  };
}

export class CommandManager {
  constructor(
    private readonly toolManager: ToolManager,
    private readonly costHooks?: CostCommandHooks,
  ) {}

  private readonly commands: CommandDefinition[] = [
    {
      name: 'help',
      description: 'Show available commands and how to use the runtime.',
      usage: '/help',
      execute: async () => ({
        output: [
          'AgentX commands:',
          '/help - show this help',
          '/tools - list registered tools',
          '/history - show the current conversation history',
          '/clear - clear the in-memory conversation history',
          '/approve - execute the currently pending risky tool call',
          '/deny - cancel the currently pending risky tool call',
          '/cost-mode - show active cost mode',
          '/cost-mode <eco|balanced|premium> - set cost mode',
          '/cost - show live cost dashboard details',
          '/vault list <password> - list stored secret keys',
          '/vault get <key> <password> - read a secret',
          '/vault add <key> <value> <password> - store a secret',
          '',
          'Natural language prompts go to the model runtime.',
        ].join('\n'),
      }),
    },
    {
      name: 'tools',
      description: 'List the currently registered tools.',
      usage: '/tools',
      execute: async () => {
        const tools = this.toolManager
          .listTools()
          .map((tool) => `- ${tool.name}: ${tool.description}`);

        return {
          output: tools.length ? tools.join('\n') : 'No tools are registered.',
        };
      },
    },
  ];

  isCommand(input: string): boolean {
    return input.trim().startsWith('/');
  }

  listCommands(): CommandDefinition[] {
    return this.commands;
  }

  async execute(input: string, context: CommandContext): Promise<CommandResult | null> {
    const trimmed = input.trim();
    if (!trimmed.startsWith('/')) {
      return null;
    }

    const withoutSlash = trimmed.slice(1);
    const [name = '', ...rest] = withoutSlash.split(/\s+/);
    const args = rest.join(' ');

    if (name === 'vault') {
      return this.handleVaultCommand(args, context);
    }

    if (name === 'cost-mode') {
      return this.handleCostModeCommand(args);
    }

    if (name === 'cost') {
      return this.handleCostCommand();
    }

    const command = this.commands.find((item) => item.name === name);
    if (!command) {
      return {
        output: `Unknown command '/${name}'. Run /help to see available commands.`,
      };
    }

    return await command.execute(args, context);
  }

  private async handleVaultCommand(args: string, context: CommandContext): Promise<CommandResult> {
    const parts = args.split(/\s+/).filter(Boolean);
    const [action, key, valueOrPassword, maybePassword] = parts;

    if (!action) {
      return { output: 'Usage: /vault <list|get|add> ...' };
    }

    if (action === 'list') {
      const password = key;
      if (!password) {
        return { output: 'Usage: /vault list <password>' };
      }

      const result = await this.toolManager.executeTool('vault', { action, password }, {
        cwd: context.cwd,
        abortSignal: AbortSignal.timeout(30_000),
        sessionId: 'command-vault-list',
      });

      return { output: String(result.output) };
    }

    if (action === 'get') {
      const password = valueOrPassword;
      if (!key || !password) {
        return { output: 'Usage: /vault get <key> <password>' };
      }

      const result = await this.toolManager.executeTool('vault', { action, key, password }, {
        cwd: context.cwd,
        abortSignal: AbortSignal.timeout(30_000),
        sessionId: 'command-vault-get',
      });

      return { output: String(result.output) };
    }

    if (action === 'add') {
      const value = valueOrPassword;
      const password = maybePassword;
      if (!key || !value || !password) {
        return { output: 'Usage: /vault add <key> <value> <password>' };
      }

      const result = await this.toolManager.executeTool('vault', { action, key, value, password }, {
        cwd: context.cwd,
        abortSignal: AbortSignal.timeout(30_000),
        sessionId: 'command-vault-add',
      });

      return { output: String(result.output) };
    }

    return { output: `Unknown vault action '${action}'.` };
  }

  private handleCostModeCommand(args: string): CommandResult {
    if (!this.costHooks) {
      return { output: 'Cost mode controls are not available in this runtime.' };
    }

    const nextMode = args.trim().toLowerCase();
    if (!nextMode) {
      return {
        output: [
          `Current cost mode: ${this.costHooks.getCostMode()}`,
          this.costHooks.getCostStatus(),
          'Usage: /cost-mode <eco|balanced|premium>',
        ].join('\n'),
      };
    }

    if (nextMode !== 'eco' && nextMode !== 'balanced' && nextMode !== 'premium') {
      return { output: `Invalid cost mode '${nextMode}'. Use eco, balanced, or premium.` };
    }

    this.costHooks.setCostMode(nextMode);
    return {
      output: [
        `Cost mode switched to: ${nextMode}`,
        this.costHooks.getCostStatus(),
      ].join('\n'),
    };
  }

  private handleCostCommand(): CommandResult {
    if (!this.costHooks) {
      return { output: 'Cost dashboard is not available in this runtime.' };
    }

    const snapshot = this.costHooks.getCostSnapshot();
    const providerLine = snapshot.providerBreakdown.length
      ? snapshot.providerBreakdown
          .map((entry) => `${entry.provider}: ${entry.percentage}% (${entry.cost < 0.01 ? `$${entry.cost.toFixed(4)}` : `$${entry.cost.toFixed(2)}`})`)
          .join(' | ')
      : 'No provider usage yet';

    const costText = snapshot.totalCost < 0.01 ? `$${snapshot.totalCost.toFixed(4)}` : `$${snapshot.totalCost.toFixed(2)}`;
    const savedText = snapshot.savedAmount < 0.01 ? `$${snapshot.savedAmount.toFixed(4)}` : `$${snapshot.savedAmount.toFixed(2)}`;

    return {
      output: [
        this.costHooks.getCostStatus(),
        `Tokens used: ${snapshot.totalTokens}`,
        `Estimated cost: ${costText}`,
        `Saved vs premium-only: ${snapshot.savedPercentage}% (${savedText})`,
        `Provider breakdown: ${providerLine}`,
      ].join('\n'),
    };
  }
}
