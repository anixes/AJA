/**
 * Agent Smart Model Router
 * 
 * Routes tasks to the optimal model based on complexity analysis.
 * Integrates with CostMode to respect the user's cost preference.
 * 
 * Routing tiers:
 *   1. Simple edits / trivial questions  → local or cheapest model
 *   2. Summaries / formatting            → cheap API model
 *   3. Medium coding tasks               → mid-tier model
 *   4. Hard architecture / complex code  → premium model
 *   5. Final review (one-shot)           → best model available
 */

import type { CostMode } from './CostMode.js';

// ── Task Complexity ──────────────────────────────────────────

export type TaskComplexity = 'trivial' | 'simple' | 'medium' | 'hard' | 'review';

// ── Route Result ─────────────────────────────────────────────

export interface RouteResult {
  /** The provider name to use (e.g. 'llama_cpp', 'openai', 'anthropic') */
  provider: string;
  /** The specific model ID to use */
  model: string;
  /** The detected task complexity tier */
  complexity: TaskComplexity;
  /** Readable reason for the routing decision */
  reason: string;
  /** Estimated cost tier label */
  costTier: 'free' | 'cheap' | 'moderate' | 'premium';
}

// ── Model Tier Configuration ─────────────────────────────────

export interface ModelTierConfig {
  provider: string;
  model: string;
}

export interface RouterConfig {
  /** Local/free model for trivial tasks */
  local: ModelTierConfig;
  /** Cheap API model for simple tasks */
  cheap: ModelTierConfig;
  /** Mid-tier model for medium complexity */
  mid: ModelTierConfig;
  /** Premium model for hard tasks */
  premium: ModelTierConfig;
  /** Best available model for final review */
  best: ModelTierConfig;
}

// ── Default Configurations ───────────────────────────────────

const DEFAULT_ROUTER_CONFIG: RouterConfig = {
  local:   { provider: 'llama_cpp', model: 'gemma-4-e2b' },
  cheap:   { provider: 'openai',    model: 'gpt-4o-mini' },
  mid:     { provider: 'openai',    model: 'gpt-4o' },
  premium: { provider: 'anthropic', model: 'claude-sonnet-4-20250514' },
  best:    { provider: 'anthropic', model: 'claude-3-opus-20240229' },
};

// ── Complexity Detection Patterns ────────────────────────────

/** Keywords / patterns that indicate trivial tasks */
const TRIVIAL_PATTERNS = [
  /^(hi|hello|hey|thanks|thank you|ok|yes|no|bye)\b/i,
  /^what (is|are) (the )?(time|date|day)/i,
  /^(rename|typo|fix typo|change name|update variable)/i,
  /simple (edit|change|rename|fix)/i,
  /^add (a )?comment/i,
  /^remove (a )?comment/i,
  /format(ting)?/i,
];

/** Keywords indicating simple / summary tasks */
const SIMPLE_PATTERNS = [
  /summar(y|ize|ise)/i,
  /explain (this|the|what)/i,
  /what does .+ (do|mean)/i,
  /^list (all|the)/i,
  /^describe/i,
  /^show me/i,
  /translate/i,
  /convert .+ to/i,
  /how (do|to) (I )?(use|run|start)/i,
];

/** Keywords indicating medium complexity (coding tasks) */
const MEDIUM_PATTERNS = [
  /implement/i,
  /add (a |the )?(new )?(feature|function|method|endpoint|route|component)/i,
  /write (a |the )?(function|method|class|test|script)/i,
  /refactor/i,
  /fix (the |this |a )?(bug|error|issue|problem)/i,
  /create (a |the )?(new )?(file|module|service|component)/i,
  /update (the |this )?(logic|code|implementation)/i,
  /debug/i,
  /unit test/i,
  /integration/i,
  /api (endpoint|call|request)/i,
];

/** Keywords indicating hard / architecture-level tasks */
const HARD_PATTERNS = [
  /architect/i,
  /design (pattern|system|the )/i,
  /migration/i,
  /redesign/i,
  /security (audit|review|vulnerability)/i,
  /performance (optim|improv|tun)/i,
  /scal(e|ing|ability)/i,
  /distributed/i,
  /micro ?service/i,
  /database (schema|design|migration)/i,
  /system design/i,
  /trade.?off/i,
  /complex (algorithm|logic|system)/i,
  /multi.?(threaded|process)/i,
  /concurrency/i,
  /entire (app|application|system|codebase)/i,
];

/** Keywords indicating review / final pass */
const REVIEW_PATTERNS = [
  /^review/i,
  /final (review|check|pass)/i,
  /code review/i,
  /pull request review/i,
  /pr review/i,
  /audit/i,
  /verify (all|everything|the changes)/i,
  /double.?check/i,
];

// ── Smart Model Router ───────────────────────────────────────

export class ModelRouter {
  private config: RouterConfig;
  private availableProviders: Set<string>;
  private routingHistory: Array<{ prompt: string; result: RouteResult; timestamp: number }> = [];

  constructor(
    config?: Partial<RouterConfig>,
    availableProviders?: string[],
  ) {
    this.config = { ...DEFAULT_ROUTER_CONFIG, ...config };
    this.availableProviders = new Set(availableProviders ?? [
      'llama_cpp', 'openai', 'anthropic', 'gemini', 'openrouter',
    ]);
  }

  /**
   * Classify the complexity of a prompt/task.
   */
  classifyComplexity(prompt: string): TaskComplexity {
    const text = prompt.trim();

    // Check in order from most specific to least
    if (this.matchesPatterns(text, REVIEW_PATTERNS)) return 'review';
    if (this.matchesPatterns(text, HARD_PATTERNS)) return 'hard';
    if (this.matchesPatterns(text, MEDIUM_PATTERNS)) return 'medium';
    if (this.matchesPatterns(text, SIMPLE_PATTERNS)) return 'simple';
    if (this.matchesPatterns(text, TRIVIAL_PATTERNS)) return 'trivial';

    // Heuristics based on length and structure
    if (text.length < 30) return 'trivial';
    if (text.length < 80) return 'simple';
    if (text.length < 300) return 'medium';
    return 'hard';
  }

  /**
   * Route a task to the optimal model, respecting cost mode.
   */
  route(prompt: string, costMode: CostMode): RouteResult {
    const complexity = this.classifyComplexity(prompt);
    const result = this.selectModel(complexity, costMode);

    this.routingHistory.push({
      prompt: prompt.slice(0, 100),
      result,
      timestamp: Date.now(),
    });

    return result;
  }

  /**
   * Force a specific complexity tier (useful for final review pass).
   */
  routeWithComplexity(complexity: TaskComplexity, costMode: CostMode): RouteResult {
    return this.selectModel(complexity, costMode);
  }

  /**
   * Get routing stats for the session.
   */
  getRoutingStats(): {
    totalRouted: number;
    byComplexity: Record<TaskComplexity, number>;
    byProvider: Record<string, number>;
    byCostTier: Record<string, number>;
  } {
    const stats = {
      totalRouted: this.routingHistory.length,
      byComplexity: { trivial: 0, simple: 0, medium: 0, hard: 0, review: 0 } as Record<TaskComplexity, number>,
      byProvider: {} as Record<string, number>,
      byCostTier: {} as Record<string, number>,
    };

    for (const entry of this.routingHistory) {
      stats.byComplexity[entry.result.complexity]++;
      stats.byProvider[entry.result.provider] = (stats.byProvider[entry.result.provider] || 0) + 1;
      stats.byCostTier[entry.result.costTier] = (stats.byCostTier[entry.result.costTier] || 0) + 1;
    }

    return stats;
  }

  /**
   * Update available providers (called when registry changes).
   */
  setAvailableProviders(providers: string[]): void {
    this.availableProviders = new Set(providers);
  }

  /**
   * Update router config at runtime.
   */
  updateConfig(config: Partial<RouterConfig>): void {
    this.config = { ...this.config, ...config };
  }

  /**
   * Reset routing history.
   */
  reset(): void {
    this.routingHistory = [];
  }

  // ── Private ────────────────────────────────────────────────

  private selectModel(complexity: TaskComplexity, costMode: CostMode): RouteResult {
    // Cost mode adjusts which tier we actually use
    const effectiveTier = this.applyModePolicy(complexity, costMode);
    const tierConfig = this.resolveTier(effectiveTier);

    // Fallback if preferred provider isn't available
    const resolvedConfig = this.resolveWithFallback(tierConfig, effectiveTier);

    return {
      provider: resolvedConfig.provider,
      model: resolvedConfig.model,
      complexity,
      reason: this.buildReason(complexity, costMode, effectiveTier),
      costTier: this.getCostTier(effectiveTier),
    };
  }

  /**
   * Apply cost mode policy to adjust the complexity → model tier mapping.
   * 
   * Eco mode:     downgrades everything toward local
   * Balanced:     uses natural mapping with fallback
   * Premium:      upgrades everything toward best
   */
  private applyModePolicy(
    complexity: TaskComplexity,
    costMode: CostMode,
  ): 'local' | 'cheap' | 'mid' | 'premium' | 'best' {
    const naturalMapping: Record<TaskComplexity, 'local' | 'cheap' | 'mid' | 'premium' | 'best'> = {
      trivial: 'local',
      simple: 'cheap',
      medium: 'mid',
      hard: 'premium',
      review: 'best',
    };

    const natural = naturalMapping[complexity];

    switch (costMode) {
      case 'eco': {
        // Downgrade tiers: everything uses local or cheap
        const ecoMapping: Record<string, 'local' | 'cheap' | 'mid' | 'premium' | 'best'> = {
          local: 'local',
          cheap: 'local',
          mid: 'cheap',
          premium: 'mid',
          best: 'mid',
        };
        return ecoMapping[natural];
      }

      case 'balanced':
        // Natural mapping — uses what the complexity dictates
        return natural;

      case 'premium': {
        // Upgrade tiers: push everything toward premium/best
        const premiumMapping: Record<string, 'local' | 'cheap' | 'mid' | 'premium' | 'best'> = {
          local: 'cheap',
          cheap: 'mid',
          mid: 'premium',
          premium: 'best',
          best: 'best',
        };
        return premiumMapping[natural];
      }

      default:
        return natural;
    }
  }

  private resolveTier(tier: 'local' | 'cheap' | 'mid' | 'premium' | 'best'): ModelTierConfig {
    return this.config[tier];
  }

  /**
   * If the preferred provider isn't available, fall back to the next best option.
   */
  private resolveWithFallback(
    config: ModelTierConfig,
    tier: 'local' | 'cheap' | 'mid' | 'premium' | 'best',
  ): ModelTierConfig {
    if (this.availableProviders.has(config.provider)) {
      return config;
    }

    // Fallback chain per tier
    const fallbackChains: Record<string, ModelTierConfig[]> = {
      local: [
        { provider: 'llama_cpp', model: 'gemma-4-e2b' },
        { provider: 'llama_cpp', model: 'phi4-mini' },
        { provider: 'openai', model: 'gpt-4o-mini' },
      ],
      cheap: [
        { provider: 'openai', model: 'gpt-4o-mini' },
        { provider: 'gemini', model: 'gemini-2.0-flash' },
        { provider: 'openrouter', model: 'meta-llama/llama-3.1-8b-instruct' },
        { provider: 'llama_cpp', model: 'gemma-4-e2b' },
      ],
      mid: [
        { provider: 'openai', model: 'gpt-4o' },
        { provider: 'gemini', model: 'gemini-2.5-flash' },
        { provider: 'anthropic', model: 'claude-3-5-haiku-20241022' },
        { provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' },
      ],
      premium: [
        { provider: 'anthropic', model: 'claude-sonnet-4-20250514' },
        { provider: 'openai', model: 'gpt-4o' },
        { provider: 'gemini', model: 'gemini-2.5-pro' },
        { provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' },
      ],
      best: [
        { provider: 'anthropic', model: 'claude-3-opus-20240229' },
        { provider: 'anthropic', model: 'claude-sonnet-4-20250514' },
        { provider: 'openai', model: 'gpt-4o' },
        { provider: 'gemini', model: 'gemini-2.5-pro' },
      ],
    };

    const chain = fallbackChains[tier] || [];
    for (const fallback of chain) {
      if (this.availableProviders.has(fallback.provider)) {
        return fallback;
      }
    }

    // Last resort: whatever is available
    const anyProvider = [...this.availableProviders][0];
    if (anyProvider) {
      return { provider: anyProvider, model: config.model };
    }

    // Nothing available — return original and let the caller handle the error
    return config;
  }

  private getCostTier(tier: 'local' | 'cheap' | 'mid' | 'premium' | 'best'): 'free' | 'cheap' | 'moderate' | 'premium' {
    const mapping: Record<string, 'free' | 'cheap' | 'moderate' | 'premium'> = {
      local: 'free',
      cheap: 'cheap',
      mid: 'moderate',
      premium: 'premium',
      best: 'premium',
    };
    return mapping[tier];
  }

  private buildReason(complexity: TaskComplexity, costMode: CostMode, tier: string): string {
    const complexityLabels: Record<TaskComplexity, string> = {
      trivial: 'trivial task (local-first)',
      simple: 'simple task (cheap API)',
      medium: 'medium complexity (mid-tier)',
      hard: 'hard/architecture task (premium)',
      review: 'final review (best model)',
    };

    return `${complexityLabels[complexity]} · ${costMode} mode → ${tier} tier`;
  }

  private matchesPatterns(text: string, patterns: RegExp[]): boolean {
    return patterns.some(p => p.test(text));
  }
}
