/**
 * AgentX Autonomous Branch Mode
 * 
 * Creates a feature branch, auto-commits after each tool loop,
 * and never touches main/master/develop.
 */

import { execSync } from 'child_process';

const PROTECTED_BRANCHES = new Set(['main', 'master', 'develop', 'production', 'staging']);

export class AutonomousBranch {
  private branchName: string;
  private originalBranch: string;
  private cwd: string;
  private commitCount = 0;

  constructor(taskSlug: string, cwd: string) {
    this.cwd = cwd;
    this.originalBranch = this.getCurrentBranch();
    this.branchName = `agentx/${this.sanitizeSlug(taskSlug)}`;
  }

  /**
   * Initialize: create and switch to the feature branch.
   */
  async initialize(): Promise<string> {
    // Safety: refuse if we're already on a protected branch and can't create
    const current = this.getCurrentBranch();

    try {
      // Create the branch from current HEAD
      execSync(`git checkout -b ${this.branchName}`, {
        cwd: this.cwd, encoding: 'utf8', timeout: 10_000,
      });
      return this.branchName;
    } catch (error: any) {
      // Branch might already exist
      if (error.message.includes('already exists')) {
        execSync(`git checkout ${this.branchName}`, {
          cwd: this.cwd, encoding: 'utf8', timeout: 10_000,
        });
        return this.branchName;
      }
      throw error;
    }
  }

  /**
   * Auto-commit current changes with a descriptive message.
   */
  async autoCommit(description: string): Promise<string | null> {
    // Safety: verify we're on the correct branch
    const current = this.getCurrentBranch();
    if (PROTECTED_BRANCHES.has(current)) {
      throw new Error(`SAFETY: Refusing to commit on protected branch "${current}"`);
    }

    // Check if there are changes to commit
    const status = execSync('git status --porcelain', {
      cwd: this.cwd, encoding: 'utf8', timeout: 10_000,
    }).trim();

    if (!status) return null; // Nothing to commit

    // Stage all changes
    execSync('git add -A', { cwd: this.cwd, encoding: 'utf8', timeout: 10_000 });

    // Commit
    this.commitCount++;
    const msg = `agentx[${this.commitCount}]: ${description}`;
    const safeMsg = msg.replace(/"/g, '\\"');

    execSync(`git commit -m "${safeMsg}"`, {
      cwd: this.cwd, encoding: 'utf8', timeout: 10_000,
    });

    return msg;
  }

  /**
   * Get a summary of all changes made on this branch.
   */
  getSummary(): string {
    try {
      const log = execSync(
        `git log ${this.originalBranch}..${this.branchName} --oneline`,
        { cwd: this.cwd, encoding: 'utf8', timeout: 10_000 }
      ).trim();

      const diffStat = execSync(
        `git diff ${this.originalBranch}..${this.branchName} --stat`,
        { cwd: this.cwd, encoding: 'utf8', timeout: 10_000 }
      ).trim();

      return [
        `Branch: ${this.branchName}`,
        `Commits: ${this.commitCount}`,
        `\nCommit Log:`,
        log || '(none)',
        `\nFile Changes:`,
        diffStat || '(none)',
      ].join('\n');
    } catch {
      return `Branch: ${this.branchName} (${this.commitCount} commits)`;
    }
  }

  /**
   * Return to the original branch (does NOT merge).
   */
  async returnToOriginal(): Promise<void> {
    try {
      execSync(`git checkout ${this.originalBranch}`, {
        cwd: this.cwd, encoding: 'utf8', timeout: 10_000,
      });
    } catch {
      // If original branch doesn't work, try main
      try {
        execSync('git checkout main', { cwd: this.cwd, encoding: 'utf8', timeout: 10_000 });
      } catch {
        // Last resort: stay on current branch
      }
    }
  }

  getBranchName(): string {
    return this.branchName;
  }

  getCommitCount(): number {
    return this.commitCount;
  }

  // ── Private ─────────────────────────────────────────────────

  private getCurrentBranch(): string {
    try {
      return execSync('git rev-parse --abbrev-ref HEAD', {
        cwd: this.cwd, encoding: 'utf8', timeout: 5_000,
      }).trim();
    } catch {
      return 'unknown';
    }
  }

  private sanitizeSlug(input: string): string {
    return input
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 50);
  }
}
