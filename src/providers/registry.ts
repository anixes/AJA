/**
 * AgentX Provider Registry
 * 
 * Auto-detects and resolves providers from:
 *   1. Environment variables (AI_KEY, AI_PROVIDER, OPENAI_API_KEY, etc.)
 *   2. providers.json config file
 *   3. Fallback to local Ollama
 */

import { existsSync, readFileSync } from 'fs';
import path from 'path';
import type { IProvider, ProviderConfig, ProviderInfo } from './types.js';
import { OpenAICompatProvider } from './openai-compat.js';
import { AnthropicProvider } from './anthropic.js';
import { GeminiProvider } from './gemini.js';
import { CostTracker } from './costTracker.js';

export class ProviderRegistry {
  private providers = new Map<string, IProvider>();
  private defaultName: string | null = null;
  readonly costTracker: CostTracker;

  constructor(costTracker?: CostTracker) {
    this.costTracker = costTracker || new CostTracker();
  }

  /**
   * Register a provider instance by name.
   */
  register(name: string, provider: IProvider): void {
    this.providers.set(name, provider);
    if (!this.defaultName) this.defaultName = name;
  }

  /**
   * Resolve a provider by name or return the default.
   */
  resolve(name?: string): IProvider {
    if (name && this.providers.has(name)) {
      return this.providers.get(name)!;
    }
    if (this.defaultName && this.providers.has(this.defaultName)) {
      return this.providers.get(this.defaultName)!;
    }
    throw new Error(`No provider found${name ? ` for "${name}"` : ''}. Run: agentx config --provider`);
  }

  /**
   * List all registered providers and their status.
   */
  listProviders(): ProviderInfo[] {
    const infos: ProviderInfo[] = [];
    for (const [name, provider] of this.providers) {
      infos.push({
        name,
        type: provider.name,
        baseUrl: '',
        connected: true,
      });
    }
    return infos;
  }

  /**
   * Set the default provider.
   */
  setDefault(name: string): void {
    if (!this.providers.has(name)) {
      throw new Error(`Provider "${name}" not registered`);
    }
    this.defaultName = name;
  }

  /**
   * Auto-configure providers from environment + config file.
   * Call this once at startup.
   */
  autoDetect(cwd: string = process.cwd()): void {
    // 1. Load from providers.json
    this.loadConfigFile(cwd);

    // 2. Auto-detect from environment variables
    this.detectFromEnv();

    // 3. Fallback: llama.cpp local
    if (this.providers.size === 0) {
      this.register('llama_cpp', new OpenAICompatProvider({
        name: 'llama_cpp',
        baseUrl: 'http://localhost:8080/v1',
        defaultModel: 'gemma-4-e2b',
      }, this.costTracker));
    }
  }

  // ── Private ─────────────────────────────────────────────────

  private loadConfigFile(cwd: string): void {
    const configPath = path.join(cwd, 'providers.json');
    if (!existsSync(configPath)) return;

    try {
      const raw = readFileSync(configPath, 'utf8');
      const configs: Record<string, { url: string }> = JSON.parse(raw);

      for (const [name, cfg] of Object.entries(configs)) {
        if (!cfg.url) continue;
        // providers.json uses OpenAI-compat format
        this.register(name, new OpenAICompatProvider({
          name,
          baseUrl: cfg.url,
          apiKey: this.getKeyForProvider(name),
          defaultModel: this.getModelForProvider(name),
        }, this.costTracker));
      }
    } catch {
      // Silently skip malformed config
    }
  }

  private detectFromEnv(): void {
    const aiKey = process.env.AI_KEY || '';
    const aiProvider = process.env.AI_PROVIDER || '';
    const aiModel = process.env.AI_MODEL || '';

    // Anthropic key detection
    const anthropicKey = process.env.ANTHROPIC_API_KEY || (aiKey.startsWith('sk-ant-') ? aiKey : '');
    if (anthropicKey && !this.providers.has('anthropic')) {
      this.register('anthropic', new AnthropicProvider({
        apiKey: anthropicKey,
        defaultModel: aiProvider === 'anthropic' ? (aiModel || undefined) : undefined,
      }, this.costTracker));
    }

    // Gemini key detection
    const geminiKey = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || '';
    if (geminiKey && !this.providers.has('gemini')) {
      this.register('gemini', new GeminiProvider({
        apiKey: geminiKey,
        defaultModel: aiProvider === 'gemini' ? (aiModel || undefined) : undefined,
      }, this.costTracker));
    }

    // OpenAI key detection
    const openaiKey = process.env.OPENAI_API_KEY || (aiKey.startsWith('sk-') && !aiKey.startsWith('sk-ant-') ? aiKey : '');
    if (openaiKey && !this.providers.has('openai')) {
      this.register('openai', new OpenAICompatProvider({
        name: 'openai',
        baseUrl: 'https://api.openai.com/v1',
        apiKey: openaiKey,
        defaultModel: aiProvider === 'openai' ? (aiModel || 'gpt-4o-mini') : 'gpt-4o-mini',
      }, this.costTracker));
    }

    // OpenRouter key detection
    const orKey = process.env.OPENROUTER_API_KEY || '';
    if (orKey && !this.providers.has('openrouter')) {
      this.register('openrouter', new OpenAICompatProvider({
        name: 'openrouter',
        baseUrl: 'https://openrouter.ai/api/v1',
        apiKey: orKey,
        defaultModel: aiModel || 'anthropic/claude-3.5-sonnet',
        headers: { 'HTTP-Referer': 'https://agentx.dev', 'X-Title': 'AgentX' },
      }, this.costTracker));
    }

    // If AI_PROVIDER explicitly set, make it default
    if (aiProvider && this.providers.has(aiProvider)) {
      this.setDefault(aiProvider);
    }
  }

  private getKeyForProvider(name: string): string {
    const envMap: Record<string, string> = {
      openai: process.env.OPENAI_API_KEY || process.env.AI_KEY || '',
      anthropic: process.env.ANTHROPIC_API_KEY || '',
      gemini: process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || '',
      openrouter: process.env.OPENROUTER_API_KEY || '',
    };
    return envMap[name] || process.env.AI_KEY || '';
  }

  private getModelForProvider(name: string): string {
    const aiModel = process.env.AI_MODEL || '';
    if (aiModel && process.env.AI_PROVIDER === name) return aiModel;

    const defaults: Record<string, string> = {
      openai: 'gpt-4o-mini',
      anthropic: 'claude-sonnet-4-20250514',
      gemini: 'gemini-2.5-flash',
      llama_cpp: 'gemma-4-e2b',
    };
    return defaults[name] || 'gpt-4o-mini';
  }
}
