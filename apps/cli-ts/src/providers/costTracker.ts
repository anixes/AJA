/**
 * Agent Cost Tracker (v2 — Phase 3: Cost Intelligence)
 * 
 * Tracks token usage, estimated costs, provider breakdown, and savings.
 * Always-on by design — cost transparency is a core feature.
 * 
 * New in v2:
 *   - Premium baseline cost tracking (what it would cost in premium-only mode)
 *   - Provider breakdown (cost & tokens per provider)
 *   - Change listeners for live dashboard updates
 *   - Detailed session snapshot for the CostDashboard component
 */

import type { TokenUsage } from './types.js';

// ── Pricing Table (USD per 1M tokens) ─────────────────────────
// Updated for 2025 pricing. Add new models as they launch.

interface ModelPricing {
  input: number;   // per 1M input tokens
  output: number;  // per 1M output tokens
}

const PRICING: Record<string, ModelPricing> = {
  // OpenAI
  'gpt-4o':              { input: 2.50,  output: 10.00 },
  'gpt-4o-mini':         { input: 0.15,  output: 0.60  },
  'gpt-4-turbo':         { input: 10.00, output: 30.00 },
  'gpt-4':               { input: 30.00, output: 60.00 },
  'gpt-3.5-turbo':       { input: 0.50,  output: 1.50  },
  'o1':                  { input: 15.00, output: 60.00 },
  'o1-mini':             { input: 3.00,  output: 12.00 },
  'o3-mini':             { input: 1.10,  output: 4.40  },
  'o4-mini':             { input: 1.10,  output: 4.40  },

  // Anthropic
  'claude-sonnet-4-20250514': { input: 3.00, output: 15.00 },
  'claude-3-5-sonnet-20241022': { input: 3.00,  output: 15.00 },
  'claude-3-5-haiku-20241022':  { input: 0.80,  output: 4.00  },
  'claude-3-opus-20240229':     { input: 15.00, output: 75.00 },

  // Gemini
  'gemini-2.5-pro':      { input: 1.25,  output: 10.00 },
  'gemini-2.5-flash':    { input: 0.15,  output: 0.60  },
  'gemini-2.0-flash':    { input: 0.10,  output: 0.40  },
  'gemini-1.5-pro':      { input: 1.25,  output: 5.00  },
  'gemini-1.5-flash':    { input: 0.075, output: 0.30  },

  // DeepSeek
  'deepseek-chat':       { input: 0.14,  output: 0.28  },
  'deepseek-reasoner':   { input: 0.55,  output: 2.19  },

  // Local (free)
  'local':               { input: 0, output: 0 },
};

/** The "premium baseline" model — used to calculate savings vs premium-only mode */
const PREMIUM_BASELINE_MODEL = 'claude-sonnet-4-20250514';

// ── Session Record ────────────────────────────────────────────

export interface TurnRecord {
  model: string;
  provider: string;
  usage: TokenUsage;
  timestamp: number;
  /** What this turn would have cost on the premium baseline model */
  premiumBaselineCost: number;
}

// ── Provider Breakdown ───────────────────────────────────────

export interface ProviderBreakdown {
  provider: string;
  turns: number;
  tokens: number;
  cost: number;
  percentage: number;  // of total cost
}

// ── Session Snapshot (for dashboard) ─────────────────────────

export interface CostSnapshot {
  turns: number;
  totalTokens: number;
  totalCost: number;
  premiumBaselineCost: number;
  savedAmount: number;
  savedPercentage: number;
  providerBreakdown: ProviderBreakdown[];
  lastTurnModel: string | null;
  lastTurnCost: number;
}

// ── Cost Tracker ──────────────────────────────────────────────

export class CostTracker {
  private turns: TurnRecord[] = [];
  private listeners: Array<(snapshot: CostSnapshot) => void> = [];

  /**
   * Estimate cost for a given usage and model.
   * Returns the cost in USD.
   */
  estimateCost(model: string, usage: TokenUsage): number {
    const pricing = this.resolvePricing(model);
    if (!pricing) return 0;

    const inputCost = (usage.promptTokens / 1_000_000) * pricing.input;
    const outputCost = (usage.completionTokens / 1_000_000) * pricing.output;
    return inputCost + outputCost;
  }

  /**
   * Record a turn's usage for session tracking.
   * @param provider - The provider name (e.g. 'openai', 'anthropic', 'llama_cpp')
   */
  recordTurn(model: string, usage: TokenUsage, provider: string = 'unknown'): void {
    const cost = this.estimateCost(model, usage);
    usage.estimatedCost = cost;

    // Calculate what this would have cost on the premium baseline
    const premiumBaselineCost = this.estimateCost(PREMIUM_BASELINE_MODEL, usage);

    this.turns.push({
      model,
      provider,
      usage,
      timestamp: Date.now(),
      premiumBaselineCost,
    });

    // Notify listeners for live dashboard updates
    this.notifyListeners();
  }

  /**
   * Get total session cost.
   */
  getSessionCost(): number {
    return this.turns.reduce((sum, t) => sum + (t.usage.estimatedCost || 0), 0);
  }

  /**
   * Get total tokens used in session.
   */
  getSessionTokens(): number {
    return this.turns.reduce((sum, t) => sum + t.usage.totalTokens, 0);
  }

  /**
   * Get the premium baseline cost (what everything would cost if premium-only).
   */
  getPremiumBaselineCost(): number {
    return this.turns.reduce((sum, t) => sum + t.premiumBaselineCost, 0);
  }

  /**
   * Get the amount saved vs premium-only mode.
   */
  getSavedAmount(): number {
    return Math.max(0, this.getPremiumBaselineCost() - this.getSessionCost());
  }

  /**
   * Get savings percentage (0-100).
   */
  getSavedPercentage(): number {
    const baseline = this.getPremiumBaselineCost();
    if (baseline === 0) return 0;
    return Math.round((this.getSavedAmount() / baseline) * 100);
  }

  /**
   * Get a breakdown of cost/tokens per provider.
   */
  getProviderBreakdown(): ProviderBreakdown[] {
    const totalCost = this.getSessionCost();
    const byProvider = new Map<string, { turns: number; tokens: number; cost: number }>();

    for (const turn of this.turns) {
      const existing = byProvider.get(turn.provider) || { turns: 0, tokens: 0, cost: 0 };
      existing.turns++;
      existing.tokens += turn.usage.totalTokens;
      existing.cost += turn.usage.estimatedCost || 0;
      byProvider.set(turn.provider, existing);
    }

    return [...byProvider.entries()]
      .map(([provider, stats]) => ({
        provider,
        turns: stats.turns,
        tokens: stats.tokens,
        cost: stats.cost,
        percentage: totalCost > 0 ? Math.round((stats.cost / totalCost) * 100) : 0,
      }))
      .sort((a, b) => b.cost - a.cost);
  }

  /**
   * Get the last turn's stats.
   */
  getLastTurn(): TurnRecord | null {
    return this.turns.length > 0 ? this.turns[this.turns.length - 1]! : null;
  }

  /**
   * Get all turn records.
   */
  getTurns(): TurnRecord[] {
    return [...this.turns];
  }

  /**
   * Get a full snapshot of the session costs (for the dashboard).
   */
  getSnapshot(): CostSnapshot {
    const lastTurn = this.getLastTurn();
    return {
      turns: this.turns.length,
      totalTokens: this.getSessionTokens(),
      totalCost: this.getSessionCost(),
      premiumBaselineCost: this.getPremiumBaselineCost(),
      savedAmount: this.getSavedAmount(),
      savedPercentage: this.getSavedPercentage(),
      providerBreakdown: this.getProviderBreakdown(),
      lastTurnModel: lastTurn?.model ?? null,
      lastTurnCost: lastTurn?.usage.estimatedCost ?? 0,
    };
  }

  /**
   * Get a formatted cost summary string.
   */
  formatSessionSummary(): string {
    const cost = this.getSessionCost();
    const tokens = this.getSessionTokens();
    const turns = this.turns.length;

    if (cost === 0 && tokens > 0) {
      return `${turns} turns · ${this.formatTokens(tokens)} tokens · free (local model)`;
    }

    return `${turns} turns · ${this.formatTokens(tokens)} tokens · ${this.formatUSD(cost)}`;
  }

  /**
   * Format a savings-aware summary line.
   * Example: "Completed for $0.0032 | Saved 91%"
   */
  formatSavingsSummary(): string {
    const cost = this.getSessionCost();
    const savedPct = this.getSavedPercentage();
    const savedAmt = this.getSavedAmount();

    if (cost === 0 && this.turns.length > 0) {
      return `Completed for free | Saved 100% vs premium`;
    }

    if (savedPct > 0) {
      return `Completed for ${this.formatUSD(cost)} | Saved ${savedPct}% (${this.formatUSD(savedAmt)})`;
    }

    return `Completed for ${this.formatUSD(cost)}`;
  }

  /**
   * Format a single turn's cost for inline display.
   * Example: "$0.0023 (1,234 tokens @ gpt-4o-mini)"
   */
  formatTurnCost(model: string, usage: TokenUsage): string {
    const cost = usage.estimatedCost ?? this.estimateCost(model, usage);

    if (cost === 0) {
      return `${this.formatTokens(usage.totalTokens)} tokens @ ${model} (free)`;
    }

    return `${this.formatUSD(cost)} (${this.formatTokens(usage.totalTokens)} tokens @ ${model})`;
  }

  /**
   * Register a listener for cost changes (for live dashboard).
   */
  onChange(listener: (snapshot: CostSnapshot) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter(fn => fn !== listener);
    };
  }

  /**
   * Reset session tracking.
   */
  reset(): void {
    this.turns = [];
    this.notifyListeners();
  }

  // ── Private Helpers ───────────────────────────────────────

  private notifyListeners(): void {
    const snapshot = this.getSnapshot();
    for (const listener of this.listeners) {
      try {
        listener(snapshot);
      } catch {
        // Don't let listener errors break cost tracking
      }
    }
  }

  private resolvePricing(model: string): ModelPricing | null {
    // Direct match
    if (PRICING[model]) return PRICING[model];

    // Prefix match (e.g. "gpt-4o-2024-08-06" → "gpt-4o")
    const keys = Object.keys(PRICING).sort((a, b) => b.length - a.length);
    for (const key of keys) {
      if (model.startsWith(key)) return PRICING[key];
    }

    // Local models (llama_cpp, lm-studio, etc.)
    if (model.includes('local') || model.includes('llama_cpp') || model.includes('llama') || model.includes('gemma')) {
      return PRICING['local'];
    }

    return null;
  }

  private formatUSD(amount: number): string {
    if (amount < 0.01) {
      return `$${amount.toFixed(4)}`;
    }
    return `$${amount.toFixed(2)}`;
  }

  private formatTokens(count: number): string {
    if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
    if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
    return `${count}`;
  }
}
