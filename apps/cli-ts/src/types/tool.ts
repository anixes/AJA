import { z } from 'zod';
import type { ExecutionMode } from '../engine/executionModes.js';

export type PermissionLevel = 'default' | 'high' | 'none';

export interface ToolContext {
  cwd: string;
  abortSignal: AbortSignal;
  sessionId: string;
  approvalGranted?: boolean;
  /** Current execution mode — tools check this for permission decisions */
  mode?: ExecutionMode;
}

export interface ToolResult<T = any> {
  output: T;
  summary?: string;
  isError?: boolean;
  requiresApproval?: boolean;
  approvalCommand?: string;
  metadata?: Record<string, unknown>;
}

export interface ToolDefinition<T extends z.ZodObject<any> = any> {
  name: string;
  description: string;
  inputSchema: T;
  permissionLevel: PermissionLevel;
  call: (args: z.infer<T>, context: ToolContext) => Promise<ToolResult>;
  
  // UI Helpers (Inspired by Claude)
  renderToolUse?: (args: z.infer<T>) => string;
  renderResult?: (result: ToolResult) => string;
}
