/**
 * Agent File Guardian (v2)
 * 
 * Enhanced with safe-file classification for auto-edit-safe mode.
 * Also supports custom safe-paths via .agent/safe-paths.json.
 */

import { existsSync, readFileSync } from 'fs';
import path from 'path';
import { isFileSafeForAutoEdit } from './executionModes.js';

const SENSITIVE_FILES = [
  'agent.json',
  'vault_data.json',
  '.env',
  '.env.local',
  '.env.production',
  'packages/agent-core/agent/security/stripper.py',
  'src/tools/bashTool.ts'
];

function isSensitivePath(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, '/');
  const fileName = normalized.split('/').pop() || '';
  return SENSITIVE_FILES.some((sensitive) => sensitive === fileName || normalized.endsWith(sensitive));
}

/**
 * Load custom safe paths from .agent/safe-paths.json if it exists.
 */
function loadCustomSafePaths(cwd: string): string[] {
  try {
    const safePath = path.join(cwd, '.agent', 'safe-paths.json');
    if (existsSync(safePath)) {
      const data = JSON.parse(readFileSync(safePath, 'utf8'));
      return Array.isArray(data) ? data : [];
    }
  } catch {}
  return [];
}

export async function validateFileOperation(
  filePath: string,
  content: string,
  cwd?: string
): Promise<'ALLOW' | 'ASK' | 'DENY'> {
  // 1. Critical System Blocks — always denied
  if (isSensitivePath(filePath) || filePath.includes('.git')) {
    return 'DENY';
  }

  // 2. Structural Integrity Check (ASK)
  if (content.includes('process.exit') || content.includes('child_process')) {
    return 'ASK';
  }

  // 3. Large Scale Overwrites (ASK)
  if (content.length > 10000) {
    return 'ASK';
  }

  return 'ALLOW';
}

/**
 * Enhanced check: is this file safe for autonomous editing?
 * Combines built-in rules with custom .agent/safe-paths.json.
 */
export function isFileAutonomousSafe(filePath: string, cwd: string = process.cwd()): boolean {
  // Never auto-edit sensitive files
  if (isSensitivePath(filePath) || filePath.includes('.git')) {
    return false;
  }

  // Check built-in safe paths
  if (isFileSafeForAutoEdit(filePath)) {
    return true;
  }

  // Check custom safe paths
  const customPaths = loadCustomSafePaths(cwd);
  const normalized = filePath.replace(/\\/g, '/');
  return customPaths.some(p => normalized.includes(p));
}
