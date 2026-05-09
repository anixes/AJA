import { exec, execFile } from 'child_process';
import { existsSync } from 'fs';
import path from 'path';
import { promisify } from 'util';
import { z } from 'zod';
import { ToolDefinition, ToolResult } from '../types/tool.js';

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

type RiskDecision = 'allow' | 'ask' | 'deny';
type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

interface StripperAnalysis {
  Original?: string;
  'Env Vars'?: Record<string, string>;
  'Blocked Env Vars'?: Record<string, string>;
  Wrappers?: string[];
  'Root Binary'?: string;
  Arguments?: string;
  'Argument Tokens'?: string[];
  Operators?: string[];
  'Dangerous Patterns'?: string[];
  'Command Count'?: number;
}

interface Classification {
  decision: RiskDecision;
  level: RiskLevel;
  reasons: string[];
}

const DENY_BINARIES = new Map<string, string>([
  ['dd', 'Low-level disk writes can irreversibly destroy data.'],
  ['mkfs', 'Filesystem formatting is always blocked.'],
  ['shutdown', 'System shutdown commands are blocked.'],
  ['reboot', 'System restart commands are blocked.'],
  ['insmod', 'Kernel module insertion is blocked.'],
  ['rmmod', 'Kernel module removal is blocked.'],
  ['visudo', 'Editing sudoers is blocked.'],
  ['diskpart', 'Disk partition manipulation is blocked.'],
  ['format', 'Filesystem formatting is blocked.'],
  ['bcdedit', 'Boot configuration changes are blocked.'],
]);

const ASK_BINARIES = new Map<string, string>([
  ['rm', 'Deletes files or directories.'],
  ['mv', 'Moves or renames files.'],
  ['chmod', 'Changes file permissions.'],
  ['chown', 'Changes file ownership.'],
  ['kill', 'Terminates processes.'],
  ['pkill', 'Terminates processes by name.'],
  ['git', 'Git commands can rewrite or discard project history.'],
  ['npm', 'Package manager commands can modify the workspace.'],
  ['pnpm', 'Package manager commands can modify the workspace.'],
  ['yarn', 'Package manager commands can modify the workspace.'],
  ['pip', 'Python package installs can mutate the environment.'],
  ['pip3', 'Python package installs can mutate the environment.'],
  ['uv', 'Python environment commands can mutate the environment.'],
  ['cargo', 'Rust toolchain commands can mutate the environment.'],
  ['curl', 'Network fetches require inspection before execution.'],
  ['wget', 'Network fetches require inspection before execution.'],
  ['invoke-webrequest', 'Network fetches require inspection before execution.'],
  ['python', 'Interpreter execution can run arbitrary code.'],
  ['python3', 'Interpreter execution can run arbitrary code.'],
  ['node', 'Interpreter execution can run arbitrary code.'],
  ['bash', 'Shell execution can run arbitrary code.'],
  ['sh', 'Shell execution can run arbitrary code.'],
  ['zsh', 'Shell execution can run arbitrary code.'],
  ['pwsh', 'Shell execution can run arbitrary code.'],
  ['powershell', 'Shell execution can run arbitrary code.'],
]);

const DENY_PATTERNS = new Map<string, string>([
  ['network-pipe', 'Piping network output directly into an interpreter is blocked.'],
  ['ssh-write', 'Writing directly into SSH trust material is blocked.'],
  ['system-path-write', 'Redirecting output into protected system paths is blocked.'],
  ['command-substitution', 'Shell substitution syntax can hide unsafe behavior.'],
  ['unbalanced-shell-syntax', 'Command parsing failed due to invalid or suspicious shell syntax.'],
]);

const ASK_PATTERNS = new Map<string, string>([
  ['protected-path', 'The command targets a protected path.'],
  ['path-traversal', 'The command uses parent-directory traversal.'],
  ['recursive-delete-flag', 'The command includes recursive destructive flags.'],
]);

function toLower(value: string | undefined): string {
  return (value || '').toLowerCase();
}

function summarizeReasons(reasons: string[]): string {
  return reasons.map((reason, index) => `${index + 1}. ${reason}`).join('\n');
}

async function analyzeCommand(command: string): Promise<StripperAnalysis> {
  const stripperPath = path.join(process.cwd(), 'scripts', 'core', 'stripper.py');
  const { stdout } = await execFileAsync(resolvePythonExecutable(), [stripperPath, command], {
    windowsHide: true,
  });
  return JSON.parse(stdout) as StripperAnalysis;
}

function resolvePythonExecutable(): string {
  const candidates = [
    process.env.AGENTX_PYTHON,
    process.env.PYTHON,
    'D:\\ANACONDA py\\python.exe',
    'python',
  ].filter((value): value is string => Boolean(value));

  for (const candidate of candidates) {
    if (candidate === 'python' || existsSync(candidate)) {
      return candidate;
    }
  }

  return 'python';
}

function classifyCommand(analysis: StripperAnalysis): Classification {
  const reasons: string[] = [];
  const rootBinary = toLower(analysis['Root Binary']);
  const patterns = analysis['Dangerous Patterns'] ?? [];
  const blockedEnvVars = analysis['Blocked Env Vars'] ?? {};
  const operators = analysis.Operators ?? [];
  const commandCount = analysis['Command Count'] ?? 1;

  if (Object.keys(blockedEnvVars).length > 0) {
    reasons.push(
      `Blocked environment variables detected: ${Object.keys(blockedEnvVars).join(', ')}.`,
    );
  }

  if (commandCount > 12) {
    reasons.push(`Command chain is too large (${commandCount} segments).`);
  }

  if (DENY_BINARIES.has(rootBinary)) {
    reasons.push(DENY_BINARIES.get(rootBinary)!);
  }

  for (const pattern of patterns) {
    if (DENY_PATTERNS.has(pattern)) {
      reasons.push(DENY_PATTERNS.get(pattern)!);
    }
  }

  if (reasons.length > 0) {
    return {
      decision: 'deny',
      level: 'CRITICAL',
      reasons,
    };
  }

  const askReasons: string[] = [];
  if (ASK_BINARIES.has(rootBinary)) {
    askReasons.push(ASK_BINARIES.get(rootBinary)!);
  }

  for (const pattern of patterns) {
    if (ASK_PATTERNS.has(pattern)) {
      askReasons.push(ASK_PATTERNS.get(pattern)!);
    }
  }

  if (operators.some((operator) => ['&&', '||', ';', '|', '>', '<'].includes(operator))) {
    askReasons.push('Compound shell operators require explicit approval.');
  }

  if (commandCount > 3) {
    askReasons.push(`Command chain has ${commandCount} segments and should be reviewed.`);
  }

  if (askReasons.length > 0) {
    return {
      decision: 'ask',
      level: rootBinary === 'rm' || patterns.includes('recursive-delete-flag') ? 'HIGH' : 'MEDIUM',
      reasons: askReasons,
    };
  }

  return {
    decision: 'allow',
    level: 'LOW',
    reasons: [],
  };
}

function approvalResponse(command: string, analysis: StripperAnalysis, classification: Classification): ToolResult<string> {
  return {
    output: [
      `[APPROVAL REQUIRED] Level: ${classification.level}`,
      `Root Binary: ${analysis['Root Binary'] || 'unknown'}`,
      `Command: ${command}`,
      'Reasons:',
      summarizeReasons(classification.reasons),
      '',
      'Run /approve to execute the pending command or /deny to cancel it.',
    ].join('\n'),
    summary: `Approval required for ${analysis['Root Binary'] || 'command'}`,
    requiresApproval: true,
    approvalCommand: '/approve',
    metadata: {
      classification,
      analysis,
      command,
    },
  };
}

function denyResponse(command: string, analysis: StripperAnalysis, classification: Classification): ToolResult<string> {
  return {
    output: [
      `[SECURITY ALERT] Level: ${classification.level}`,
      `Root Binary: ${analysis['Root Binary'] || 'unknown'}`,
      `Command: ${command}`,
      'Reasons:',
      summarizeReasons(classification.reasons),
      '',
      'Execution blocked by AgentX Safety Gate.',
    ].join('\n'),
    summary: `Blocked ${analysis['Root Binary'] || 'command'}`,
    isError: true,
    metadata: {
      classification,
      analysis,
      command,
    },
  };
}

export const bashTool: ToolDefinition<any> = {
  name: 'bash',
  description: 'Execute a shell command through AgentX safety classification and approval checks.',
  inputSchema: z.object({
    command: z.string().describe('The command to execute in the terminal.'),
  }),
  permissionLevel: 'high',
  call: async ({ command }, context) => {
    try {
      const analysis = await analyzeCommand(command);
      const classification = classifyCommand(analysis);

      if (classification.decision === 'deny') {
        return denyResponse(command, analysis, classification);
      }

      if (classification.decision === 'ask' && !context.approvalGranted) {
        return approvalResponse(command, analysis, classification);
      }

      const safeEnvVars = analysis['Env Vars'] ?? {};
      const { stdout, stderr } = await execAsync(command, {
        cwd: context.cwd,
        signal: context.abortSignal,
        env: { ...process.env, ...safeEnvVars },
      });

      const output = stderr ? `${stdout}\nErrors:\n${stderr}` : stdout;
      return {
        output,
        summary: `Executed: ${command.slice(0, 50)}${command.length > 50 ? '...' : ''}`,
        isError: Boolean(stderr),
        metadata: {
          classification,
          analysis,
        },
      };
    } catch (error: any) {
      return {
        output: `Execution failed: ${error.message}`,
        isError: true,
      };
    }
  },
};
