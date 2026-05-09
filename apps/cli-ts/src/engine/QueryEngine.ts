import { CommandManager } from '../commands/index.js';
import { GatewayClient, GatewayMessage, GatewayUsage } from '../services/gatewayClient.js';
import { RuntimeApproval, RuntimeEvent, RuntimeStateStore } from '../services/runtimeState.js';
import { ToolContext, ToolResult } from '../types/tool.js';
import { ToolManager } from './ToolManager.js';
import { CostMode, CostModeManager } from './CostMode.js';
import { ModelRouter, RouteResult } from './ModelRouter.js';

export interface Message {
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  tool_use?: {
    name: string;
    input: Record<string, unknown>;
  };
}

export interface ModelDirective {
  assistant_message: string;
  tool_call?: {
    name: string;
    input: Record<string, unknown>;
  };
}

interface PendingApproval {
  name: string;
  input: Record<string, unknown>;
  message: string;
}

const SYSTEM_PROMPT = `You are AgentX, a security-first coding assistant.
You can either reply directly or request exactly one tool call at a time.
Return only valid JSON in this shape:
{"assistant_message":"text for the user","tool_call":{"name":"tool-name","input":{"key":"value"}}}
If no tool is needed, omit tool_call.
Keep assistant_message concise and explain the purpose of any tool you request.`;

export class QueryEngine {
  protected readonly history: Message[] = [];
  protected readonly gateway = new GatewayClient();
  protected readonly commands: CommandManager;
  protected readonly runtimeState = new RuntimeStateStore();
  protected readonly costModeManager: CostModeManager;
  protected readonly modelRouter: ModelRouter;
  protected lastUsage: GatewayUsage | undefined;
  protected pendingApproval: PendingApproval | undefined;
  protected currentTurnSavedTokens: number = 0;
  protected lastRoute: RouteResult | undefined;

  constructor(private readonly toolManager: ToolManager) {
    const initialMode = this.resolveInitialCostMode();
    this.costModeManager = new CostModeManager(initialMode);
    this.modelRouter = new ModelRouter(undefined, this.gateway.getRegistry().listProviders().map((p) => p.name));
    this.commands = new CommandManager(toolManager, {
      getCostMode: () => this.getCostMode(),
      setCostMode: (mode) => this.setCostMode(mode),
      getCostStatus: () => this.costModeManager.formatStatus(this.getCostTracker().getSessionCost()),
      getCostSnapshot: () => this.getCostTracker().getSnapshot(),
    });
  }

  async query(prompt: string): Promise<string> {
    const trimmed = prompt.trim();
    if (!trimmed) {
      return 'Enter a prompt or run /help.';
    }

    if (this.commands.isCommand(trimmed)) {
      return await this.handleCommand(trimmed);
    }

    // Try to get relevant context
    try {
      const { getGraphContext } = await import('../commands/shared.js');
      const contextBlock = await getGraphContext(trimmed);
      
      if (contextBlock) {
        this.history.push({ role: 'system', content: `Background context for next query:\n${contextBlock}` });
      }
    } catch (error) {
      // Silently ignore context retrieval errors so the main query isn't halted
      // console.error(`[Graph Context Error] Failed to retrieve context: ${error}`);
    }

    this.history.push({ role: 'user', content: trimmed });

    if (!this.gateway.isConfigured()) {
      const availableTools = this.toolManager.listTools().map((tool) => tool.name).join(', ');
      const guidance = [
        'The model runtime is not configured yet.',
        'Set `AI_KEY` (or `OPENAI_API_KEY`) and optionally `AI_MODEL` / `AI_PROVIDER` to enable live responses.',
        availableTools ? `Registered tools: ${availableTools}` : 'No tools are registered yet.',
        'You can still use /help, /tools, /approve, /deny, and /vault commands.',
      ].join('\n');

      this.history.push({ role: 'assistant', content: guidance });
      return guidance;
    }

    try {
      return await this.runToolLoop();
    } catch (error) {
      const message = `AgentX runtime error: ${error instanceof Error ? error.message : String(error)}`;
      this.history.push({ role: 'assistant', content: message });
      return message;
    }
  }

  getHistory(): Message[] {
    return this.history;
  }

  getCostTracker() {
    return this.gateway.getCostTracker();
  }

  getCostMode(): CostMode {
    return this.costModeManager.getMode();
  }

  setCostMode(mode: CostMode): void {
    this.costModeManager.setMode(mode);
  }

  getLastRoute(): RouteResult | undefined {
    return this.lastRoute;
  }

  clearHistory(): void {
    this.history.length = 0;
    this.lastUsage = undefined;
    this.pendingApproval = undefined;
    this.lastRoute = undefined;
    this.runtimeState.clear();
  }

  protected async handleCommand(input: string): Promise<string> {
    if (input === '/history') {
      return this.renderHistory();
    }

    if (input === '/clear') {
      this.clearHistory();
      return 'Conversation history cleared.';
    }

    if (input === '/approve') {
      return await this.approvePendingTool();
    }

    if (input === '/deny') {
      return this.denyPendingTool();
    }

    const result = await this.commands.execute(input, { cwd: process.cwd() });
    return result?.output ?? 'Command could not be processed.';
  }

  private renderHistory(): string {
    const lines: string[] = [];
    if (!this.history.length) {
      lines.push('No conversation history yet.');
    } else {
      lines.push(
        ...this.history.map((entry, index) => {
          const prefix = `${index + 1}. ${entry.role.toUpperCase()}`;
          if (entry.tool_use) {
            return `${prefix}: ${entry.tool_use.name} ${JSON.stringify(entry.tool_use.input)}`;
          }
          return `${prefix}: ${entry.content}`;
        }),
      );
    }

    if (this.pendingApproval) {
      lines.push(`Pending approval: ${this.pendingApproval.name} ${JSON.stringify(this.pendingApproval.input)}`);
    }

    if (this.lastUsage?.total_tokens) {
      lines.push(`Usage: ${this.lastUsage.total_tokens} total tokens in the last model turn.`);
    }

    return lines.join('\n');
  }

  protected async runToolLoop(): Promise<string> {
    let finalMessage = '';

    for (let attempt = 0; attempt < 4; attempt += 1) {
      const directive = await this.requestDirective();
      if (!directive.tool_call) {
        finalMessage = directive.assistant_message;
        this.history.push({ role: 'assistant', content: finalMessage });
        return finalMessage;
      }

      const toolPrelude = directive.assistant_message || `Running ${directive.tool_call.name}...`;
      this.history.push({
        role: 'assistant',
        content: toolPrelude,
        tool_use: directive.tool_call,
      });

      const toolResult = await this.executeTool(directive.tool_call.name, directive.tool_call.input);
      this.history.push({
        role: 'tool',
        content: typeof toolResult.output === 'string' ? toolResult.output : JSON.stringify(toolResult.output),
      });

      this.recordToolEvent(directive.tool_call.name, directive.tool_call.input, toolResult);

      if (toolResult.requiresApproval) {
        this.pendingApproval = {
          name: directive.tool_call.name,
          input: directive.tool_call.input,
          message: String(toolResult.output),
        };
        this.runtimeState.setPendingApproval(
          this.buildPendingApproval(directive.tool_call.name, directive.tool_call.input, toolResult),
        );
        return String(toolResult.output);
      }

      this.runtimeState.setPendingApproval(null);

      if (this.lastUsage) {
        this.runtimeState.addTokenStats({
          total: this.lastUsage.total_tokens || 0,
          saved: this.currentTurnSavedTokens,
          lastTurn: this.lastUsage.total_tokens || 0
        });
      }
    }

    finalMessage = 'The agent reached the tool-call limit for this turn. Please refine the request and try again.';
    this.history.push({ role: 'assistant', content: finalMessage });
    return finalMessage;
  }

  protected async requestDirective(): Promise<ModelDirective> {
    const toolSummary = this.toolManager
      .listTools()
      .map((tool) => ({
        name: tool.name,
        description: tool.description,
      }));

    const approvalSummary = this.pendingApproval
      ? `There is already a pending approval for ${this.pendingApproval.name}. Ask the user to run /approve or /deny before requesting more risky tool calls.`
      : 'No tool approvals are currently pending.';

    const MAX_HISTORY = 12;
    const prunedHistory = this.history.length > MAX_HISTORY 
        ? this.history.slice(-MAX_HISTORY) 
        : this.history;

    const prunedCount = this.history.length - prunedHistory.length;
    let savedTokens = 0;
    if (prunedCount > 0) {
      const prunedMessages = this.history.slice(0, prunedCount);
      savedTokens = Math.floor(prunedMessages.reduce((acc, m) => acc + (m.content?.length || 0), 0) / 4);
    }
    this.currentTurnSavedTokens = savedTokens;

    const messages: GatewayMessage[] = [
      {
        role: 'system',
        content: `${SYSTEM_PROMPT}\nAvailable tools:\n${JSON.stringify(toolSummary)}\n${approvalSummary}`,
      },
      ...prunedHistory.map((message) => this.toGatewayMessage(message)),
    ];

    this.modelRouter.setAvailableProviders(this.gateway.getRegistry().listProviders().map((p) => p.name));
    const latestUserPrompt = [...prunedHistory].reverse().find((m) => m.role === 'user')?.content || '';
    const route = this.modelRouter.route(latestUserPrompt, this.costModeManager.getMode());
    this.lastRoute = route;

    const response = await this.gateway.chat(messages, {
      providerName: route.provider,
      model: route.model,
    });
    this.lastUsage = response.usage;

    return this.parseDirective(response.content);
  }

  private resolveInitialCostMode(): CostMode {
    const raw = (process.env['AGENTX_COST_MODE'] || process.env['COST_MODE'] || 'balanced').toLowerCase();
    if (raw === 'eco' || raw === 'balanced' || raw === 'premium') {
      return raw;
    }
    return 'balanced';
  }

  private parseDirective(raw: string): ModelDirective {
    try {
      const parsed = JSON.parse(raw) as ModelDirective;
      if (!parsed.assistant_message) {
        throw new Error('assistant_message is required.');
      }
      return parsed;
    } catch {
      return { assistant_message: raw };
    }
  }

  private async executeTool(
    name: string,
    input: Record<string, unknown>,
    approvalGranted = false,
  ): Promise<ToolResult> {
    const context: ToolContext = {
      cwd: process.cwd(),
      abortSignal: AbortSignal.timeout(30_000),
      sessionId: `agentx-${Date.now()}`,
      approvalGranted,
    };

    return await this.toolManager.executeTool(name, input, context);
  }

  private async approvePendingTool(): Promise<string> {
    if (!this.pendingApproval) {
      return 'There is no pending tool approval.';
    }

    const pending = this.pendingApproval;
    this.pendingApproval = undefined;

    const result = await this.executeTool(pending.name, pending.input, true);
    const output = typeof result.output === 'string' ? result.output : JSON.stringify(result.output);
    this.runtimeState.setPendingApproval(null);
    this.runtimeState.addEvent({
      id: `event-${Date.now()}`,
      type: 'APPROVED',
      tool: pending.name,
      message: `Approved execution for ${pending.name}.`,
      command: this.extractCommand(pending.input),
      createdAt: new Date().toISOString(),
    });

    this.history.push({
      role: 'assistant',
      content: `User approved ${pending.name}.`,
      tool_use: {
        name: pending.name,
        input: pending.input,
      },
    });
    this.history.push({
      role: 'tool',
      content: output,
    });

    this.recordToolEvent(pending.name, pending.input, result);

    return output;
  }

  private denyPendingTool(): string {
    if (!this.pendingApproval) {
      return 'There is no pending tool approval.';
    }

    const denied = this.pendingApproval;
    this.pendingApproval = undefined;
    this.runtimeState.setPendingApproval(null);
    const message = `Pending tool call denied: ${denied.name}.`;
    this.runtimeState.addEvent({
      id: `event-${Date.now()}`,
      type: 'DENIED',
      tool: denied.name,
      message,
      command: this.extractCommand(denied.input),
      createdAt: new Date().toISOString(),
    });
    this.history.push({ role: 'assistant', content: message });
    return message;
  }

  private toGatewayMessage(message: Message): GatewayMessage {
    if (message.role === 'tool') {
      return {
        role: 'user',
        content: `Tool result:\n${message.content}`,
      };
    }

    return {
      role: message.role,
      content: message.content,
    };
  }

  private recordToolEvent(name: string, input: Record<string, unknown>, result: ToolResult): void {
    const metadata = result.metadata ?? {};
    const classification = (metadata.classification ?? {}) as Record<string, unknown>;
    const analysis = (metadata.analysis ?? {}) as Record<string, unknown>;
    const eventType = result.requiresApproval
      ? 'ASK'
      : result.isError
        ? 'DENY'
        : 'ALLOW';

    const event: RuntimeEvent = {
      id: `event-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      type: eventType,
      tool: name,
      message: String(result.summary || result.output),
      command: this.extractCommand(input),
      rootBinary: typeof analysis['Root Binary'] === 'string' ? analysis['Root Binary'] : undefined,
      level: typeof classification.level === 'string' ? classification.level : undefined,
      createdAt: new Date().toISOString(),
    };

    this.runtimeState.addEvent(event);
  }

  private buildPendingApproval(
    name: string,
    input: Record<string, unknown>,
    result: ToolResult,
  ): RuntimeApproval {
    const metadata = result.metadata ?? {};
    const classification = (metadata.classification ?? {}) as Record<string, unknown>;
    const analysis = (metadata.analysis ?? {}) as Record<string, unknown>;
    const reasons = Array.isArray(classification.reasons)
      ? classification.reasons.filter((value): value is string => typeof value === 'string')
      : [];

    return {
      id: `approval-${Date.now()}`,
      tool: name,
      input,
      command: this.extractCommand(input),
      commandPreview: this.extractCommand(input) || JSON.stringify(input),
      actionType: name === 'bash' ? 'shell_command' : name,
      rootBinary: typeof analysis['Root Binary'] === 'string' ? analysis['Root Binary'] : undefined,
      level: typeof classification.level === 'string' ? classification.level : undefined,
      riskLevel: this.normalizeRiskLevel(classification.level),
      reasons,
      humanReason: reasons[0] || 'This action changes system or workspace state and needs review.',
      rollbackPath: this.buildRollbackPath(name, input),
      expiresAt: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
      requesterSource: 'CLI',
      dryRunSummary: this.buildDryRunSummary(name, input),
      createdAt: new Date().toISOString(),
    };
  }

  private extractCommand(input: Record<string, unknown>): string | undefined {
    return typeof input.command === 'string' ? input.command : undefined;
  }

  private normalizeRiskLevel(level: unknown): 'low' | 'medium' | 'high' {
    const normalized = String(level || '').toLowerCase();
    if (normalized === 'critical' || normalized === 'high') return 'high';
    if (normalized === 'medium') return 'medium';
    return 'low';
  }

  private buildRollbackPath(name: string, input: Record<string, unknown>): string {
    const command = this.extractCommand(input)?.toLowerCase() || '';
    if (name === 'bash' && command.startsWith('git pull')) {
      return 'Use git reflog to identify the previous HEAD, then reset only after reviewing local changes.';
    }
    if (name === 'bash' && command.includes('shutdown')) {
      return 'Run shutdown /a before the timer expires.';
    }
    return 'No automatic rollback is known. Review output and restore from version control or backups if needed.';
  }

  private buildDryRunSummary(name: string, input: Record<string, unknown>): string {
    const command = this.extractCommand(input);
    if (command) {
      return `Would execute: ${command}`;
    }
    return `Would run ${name} with input ${JSON.stringify(input).slice(0, 300)}`;
  }
}
