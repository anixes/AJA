/**
 * AgentX File Guardian (v2)
 * 
 * Enhanced with safe-file classification for auto-edit-safe mode.
 * Also supports custom safe-paths via .agentx/safe-paths.json.
 */

import { existsSync, readFileSync } from 'fs';
import path from 'path';
import { isFileSafeForAutoEdit } from './executionModes.js';

const SENSITIVE_FILES = [
  'agentx.json',
  'vault_data.json',
  '.env',
  '.env.local',
  '.env.production',
  'scripts/stripper.py',
  'src/tools/bashTool.ts'
];

/**
 * Load custom safe paths from .agentx/safe-paths.json if it exists.
 */
function loadCustomSafePaths(cwd: string): string[] {
  try {
    const safePath = path.join(cwd, '.agentx', 'safe-paths.json');
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
  const fileName = filePath.split(/[/\\]/).pop() || '';
  
  // 1. Critical System Blocks — always denied
  if (SENSITIVE_FILES.includes(fileName) || filePath.includes('.git')) {
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
 * Combines built-in rules with custom .agentx/safe-paths.json.
 */
export function isFileAutonomousSafe(filePath: string, cwd: string = process.cwd()): boolean {
  const fileName = filePath.split(/[/\\]/).pop() || '';

  // Never auto-edit sensitive files
  if (SENSITIVE_FILES.includes(fileName) || filePath.includes('.git')) {
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
