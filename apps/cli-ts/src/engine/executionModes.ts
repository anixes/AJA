/**
 * AgentX Execution Modes
 * 
 * 5 safety levels from read-only to fully autonomous.
 * Each mode defines what operations are permitted without user approval.
 */

export type ExecutionMode =
  | 'read-only'
  | 'suggest-only'
  | 'ask-before-edit'
  | 'auto-edit-safe'
  | 'autonomous-branch';

export interface ModePolicy {
  /** Descriptive label */
  label: string;
  /** Can read files */
  canReadFiles: boolean;
  /** Can write files: true = always, 'safe-only' = safe files only, 'approval' = needs approval, false = never */
  canWriteFiles: boolean | 'safe-only' | 'approval';
  /** Can execute bash: true = always, 'safe-only' = safe cmds only, 'approval' = needs approval, false = never */
  canExecuteBash: boolean | 'safe-only' | 'approval';
  /** Can perform git write operations */
  canGitWrite: boolean | 'feature-branch-only';
  /** Can the agent auto-commit changes */
  canAutoCommit: boolean;
}

/**
 * Policy definitions for each execution mode.
 */
export const MODE_POLICIES: Record<ExecutionMode, ModePolicy> = {
  'read-only': {
    label: 'Read Only',
    canReadFiles: true,
    canWriteFiles: false,
    canExecuteBash: false,
    canGitWrite: false,
    canAutoCommit: false,
  },

  'suggest-only': {
    label: 'Suggest Only',
    canReadFiles: true,
    canWriteFiles: false,      // Returns diffs instead of applying
    canExecuteBash: false,
    canGitWrite: false,
    canAutoCommit: false,
  },

  'ask-before-edit': {
    label: 'Ask Before Edit',
    canReadFiles: true,
    canWriteFiles: 'approval',
    canExecuteBash: 'approval',
    canGitWrite: false,
    canAutoCommit: false,
  },

  'auto-edit-safe': {
    label: 'Auto-Edit Safe Files',
    canReadFiles: true,
    canWriteFiles: 'safe-only',
    canExecuteBash: 'safe-only',
    canGitWrite: false,
    canAutoCommit: false,
  },

  'autonomous-branch': {
    label: 'Autonomous Branch',
    canReadFiles: true,
    canWriteFiles: true,
    canExecuteBash: true,
    canGitWrite: 'feature-branch-only',
    canAutoCommit: true,
  },
};

/**
 * Safe file paths for auto-edit-safe mode.
 * Files in these directories can be auto-edited without approval.
 */
const SAFE_PATHS = [
  'src/', 'lib/', 'app/', 'pages/', 'components/',
  'utils/', 'helpers/', 'hooks/', 'services/',
  'tests/', '__tests__/', 'test/', 'spec/',
  'styles/', 'css/',
];

/**
 * Files that are NEVER auto-editable regardless of mode.
 */
const NEVER_AUTO_EDIT = [
  '.env', '.env.local', '.env.production',
  'package.json', 'package-lock.json', 'yarn.lock', 'bun.lockb',
  'tsconfig.json', 'webpack.config.js', 'vite.config.ts',
  '.gitignore', '.npmrc', '.nvmrc',
  'Dockerfile', 'docker-compose.yml',
  'agentx.json', 'providers.json',
];

/**
 * Check if a file path is considered "safe" for auto-editing.
 */
export function isFileSafeForAutoEdit(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, '/');

  // Never auto-edit critical files
  const fileName = normalized.split('/').pop() || '';
  if (NEVER_AUTO_EDIT.includes(fileName)) return false;

  // Check if path is in a safe directory
  return SAFE_PATHS.some(safePath => normalized.includes(safePath));
}

/**
 * Check if a tool operation is allowed under the current mode.
 * Returns: 'allow' | 'deny' | 'approval-needed'
 */
export function checkModePermission(
  mode: ExecutionMode,
  operation: 'read-file' | 'write-file' | 'bash' | 'git-write' | 'git-commit',
  context?: { filePath?: string }
): 'allow' | 'deny' | 'approval-needed' {
  const policy = MODE_POLICIES[mode];

  switch (operation) {
    case 'read-file':
      return policy.canReadFiles ? 'allow' : 'deny';

    case 'write-file': {
      if (policy.canWriteFiles === true) return 'allow';
      if (policy.canWriteFiles === false) return 'deny';
      if (policy.canWriteFiles === 'approval') return 'approval-needed';
      if (policy.canWriteFiles === 'safe-only') {
        return context?.filePath && isFileSafeForAutoEdit(context.filePath)
          ? 'allow' : 'approval-needed';
      }
      return 'deny';
    }

    case 'bash': {
      if (policy.canExecuteBash === true) return 'allow';
      if (policy.canExecuteBash === false) return 'deny';
      if (policy.canExecuteBash === 'approval') return 'approval-needed';
      if (policy.canExecuteBash === 'safe-only') return 'approval-needed'; // bash safety handled by stripper.py
      return 'deny';
    }

    case 'git-write': {
      if (policy.canGitWrite === true) return 'allow';
      if (policy.canGitWrite === false) return 'deny';
      if (policy.canGitWrite === 'feature-branch-only') return 'allow'; // Branch check done elsewhere
      return 'deny';
    }

    case 'git-commit':
      return policy.canAutoCommit ? 'allow' : 'approval-needed';

    default:
      return 'deny';
  }
}

/**
 * Get the default execution mode.
 */
export function getDefaultMode(): ExecutionMode {
  const envMode = process.env.AGENTX_MODE as ExecutionMode;
  if (envMode && MODE_POLICIES[envMode]) return envMode;
  return 'ask-before-edit';
}
