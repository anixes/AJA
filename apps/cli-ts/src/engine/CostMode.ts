/**
 * AgentX Cost Modes
 * 
 * Defines three cost strategies that control how the Smart Model Router
 * selects models for each task:
 * 
 *   eco      — Local-first. Prefer free/local models, only use API for hard tasks.
 *   balanced — Natural mapping. Local for trivial, cheap for simple, mid for medium, etc.
 *   premium  — Best-first. Always upgrade to the next tier for maximum quality.
 */

// ── Cost Mode Type ───────────────────────────────────────────

export type CostMode = 'eco' | 'balanced' | 'premium';

// ── Cost Mode Metadata ───────────────────────────────────────

export interface CostModeInfo {
  name: CostMode;
  label: string;
  description: string;
  /** Approximate cost multiplier relative to balanced (1.0x) */
  costMultiplier: number;
  /** Visual indicator for TUI */
  icon: string;
  /** Color hint for UI rendering */
  color: string;
}

export const COST_MODES: Record<CostMode, CostModeInfo> = {
  eco: {
    name: 'eco',
    label: 'Eco Mode',
    description: 'Local-first. Uses free/local models whenever possible. API calls only for complex tasks.',
    costMultiplier: 0.1,
    icon: '🌱',
    color: 'green',
  },
  balanced: {
    name: 'balanced',
    label: 'Balanced Mode',
    description: 'Smart routing. Matches model tier to task complexity for optimal cost/quality trade-off.',
    costMultiplier: 1.0,
    icon: '⚖️',
    color: 'yellow',
  },
  premium: {
    name: 'premium',
    label: 'Premium Mode',
    description: 'Best-first. Always uses the highest quality model available for maximum accuracy.',
    costMultiplier: 5.0,
    icon: '💎',
    color: 'magenta',
  },
};

// ── Cost Budget ──────────────────────────────────────────────

export interface CostBudget {
  /** Maximum session cost in USD. null = unlimited. */
  maxSessionCost: number | null;
  /** Warning threshold as fraction of max (e.g. 0.8 = warn at 80%) */
  warningThreshold: number;
}

const DEFAULT_BUDGETS: Record<CostMode, CostBudget> = {
  eco: {
    maxSessionCost: 0.10,
    warningThreshold: 0.8,
  },
  balanced: {
    maxSessionCost: 1.00,
    warningThreshold: 0.8,
  },
  premium: {
    maxSessionCost: null,
    warningThreshold: 0.8,
  },
};

// ── Cost Mode Manager ────────────────────────────────────────

export class CostModeManager {
  private currentMode: CostMode;
  private budget: CostBudget;
  private listeners: Array<(mode: CostMode) => void> = [];

  constructor(initialMode: CostMode = 'balanced') {
    this.currentMode = initialMode;
    this.budget = { ...DEFAULT_BUDGETS[initialMode] };
  }

  /**
   * Get the current cost mode.
   */
  getMode(): CostMode {
    return this.currentMode;
  }

  /**
   * Get full info about the current mode.
   */
  getModeInfo(): CostModeInfo {
    return COST_MODES[this.currentMode];
  }

  /**
   * Switch to a different cost mode.
   */
  setMode(mode: CostMode): void {
    this.currentMode = mode;
    this.budget = { ...DEFAULT_BUDGETS[mode] };
    this.listeners.forEach(fn => fn(mode));
  }

  /**
   * Cycle to the next mode: eco → balanced → premium → eco
   */
  cycleMode(): CostMode {
    const order: CostMode[] = ['eco', 'balanced', 'premium'];
    const idx = order.indexOf(this.currentMode);
    const next = order[(idx + 1) % order.length];
    this.setMode(next);
    return next;
  }

  /**
   * Get the current budget configuration.
   */
  getBudget(): CostBudget {
    return { ...this.budget };
  }

  /**
   * Override the budget for the current session.
   */
  setBudget(budget: Partial<CostBudget>): void {
    this.budget = { ...this.budget, ...budget };
  }

  /**
   * Check if the current session cost has exceeded or is approaching the budget.
   */
  checkBudget(currentCost: number): {
    exceeded: boolean;
    warning: boolean;
    remaining: number | null;
    message: string | null;
  } {
    const { maxSessionCost, warningThreshold } = this.budget;

    if (maxSessionCost === null) {
      return { exceeded: false, warning: false, remaining: null, message: null };
    }

    const remaining = maxSessionCost - currentCost;
    const exceeded = currentCost >= maxSessionCost;
    const warning = !exceeded && currentCost >= maxSessionCost * warningThreshold;

    let message: string | null = null;
    if (exceeded) {
      message = `Budget exceeded: $${currentCost.toFixed(4)} / $${maxSessionCost.toFixed(2)}. Switch to eco mode or increase budget.`;
    } else if (warning) {
      message = `Budget warning: $${currentCost.toFixed(4)} / $${maxSessionCost.toFixed(2)} (${Math.round((currentCost / maxSessionCost) * 100)}% used)`;
    }

    return { exceeded, warning, remaining, message };
  }

  /**
   * Register a listener for mode changes.
   */
  onModeChange(listener: (mode: CostMode) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(fn => fn !== listener);
    };
  }

  /**
   * Format current mode for display.
   */
  formatMode(): string {
    const info = COST_MODES[this.currentMode];
    return `${info.icon} ${info.label}`;
  }

  /**
   * Format mode + budget status for display.
   */
  formatStatus(currentCost: number): string {
    const info = COST_MODES[this.currentMode];
    const budgetCheck = this.checkBudget(currentCost);

    let budgetStr = '';
    if (this.budget.maxSessionCost !== null) {
      budgetStr = ` · budget: $${currentCost.toFixed(4)} / $${this.budget.maxSessionCost.toFixed(2)}`;
      if (budgetCheck.exceeded) budgetStr += ' EXCEEDED';
      else if (budgetCheck.warning) budgetStr += ' ⚠';
    }

    return `${info.icon} ${info.label}${budgetStr}`;
  }
}
