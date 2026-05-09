/**
 * AgentX Gateway Client (v2)
 * 
 * Upgraded to delegate to the ProviderRegistry.
 * Backward-compatible: GatewayMessage/GatewayResponse interfaces unchanged
 * so existing QueryEngine code keeps working.
 */

import { ProviderRegistry } from '../providers/registry.js';
import { CostTracker } from '../providers/costTracker.js';
import type { ChatMessage, StreamChunk, ToolSchema, ToolCallResponse } from '../providers/types.js';

// ── Legacy interfaces (kept for backward compat) ──────────────

export interface GatewayMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
}

export interface GatewayUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface GatewayResponse {
  content: string;
  usage?: GatewayUsage;
}

export interface GatewayChatOptions {
  providerName?: string;
  model?: string;
}

// ── Gateway Client ────────────────────────────────────────────

export class GatewayClient {
  private registry: ProviderRegistry;

  constructor(registry?: ProviderRegistry) {
    const costTracker = new CostTracker();
    this.registry = registry || new ProviderRegistry(costTracker);
    this.registry.autoDetect();
  }

  /**
   * Check if any provider is configured.
   */
  isConfigured(): boolean {
    try {
      this.registry.resolve();
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Get the underlying provider registry (for direct access to streaming, tool calls, etc.)
   */
  getRegistry(): ProviderRegistry {
    return this.registry;
  }

  /**
   * Get the cost tracker instance.
   */
  getCostTracker(): CostTracker {
    return this.registry.costTracker;
  }

  /**
   * Standard chat (backward-compatible with existing QueryEngine).
   * Maps GatewayMessage[] → ChatMessage[] and back.
   */
  async chat(messages: GatewayMessage[], providerNameOrOptions?: string | GatewayChatOptions, model?: string): Promise<GatewayResponse> {
    const options: GatewayChatOptions = typeof providerNameOrOptions === 'string'
      ? { providerName: providerNameOrOptions, model }
      : (providerNameOrOptions || {});

    const provider = this.registry.resolve(options.providerName);

    const chatMessages: ChatMessage[] = messages.map(m => ({
      role: m.role,
      content: m.content,
    }));

    const response = await provider.chat(chatMessages, {
      model: options.model,
    });

    return {
      content: response.content,
      usage: {
        prompt_tokens: response.usage.promptTokens,
        completion_tokens: response.usage.completionTokens,
        total_tokens: response.usage.totalTokens,
      },
    };
  }

  /**
   * Streaming chat — yields deltas as they arrive.
   */
  async *stream(messages: GatewayMessage[], providerName?: string): AsyncIterable<StreamChunk> {
    const provider = this.registry.resolve(providerName);

    const chatMessages: ChatMessage[] = messages.map(m => ({
      role: m.role,
      content: m.content,
    }));

    yield* provider.stream(chatMessages);
  }

  /**
   * Chat with tool/function calling support.
   */
  async toolCall(
    messages: GatewayMessage[],
    tools: ToolSchema[],
    providerName?: string
  ): Promise<ToolCallResponse> {
    const provider = this.registry.resolve(providerName);

    const chatMessages: ChatMessage[] = messages.map(m => ({
      role: m.role,
      content: m.content,
    }));

    return provider.toolCall(chatMessages, tools);
  }

  /**
   * Simple text completion.
   */
  async complete(prompt: string, providerName?: string): Promise<string> {
    const provider = this.registry.resolve(providerName);
    return provider.complete(prompt);
  }

  /**
   * Generate embeddings.
   */
  async embeddings(texts: string[], providerName?: string): Promise<number[][]> {
    const provider = this.registry.resolve(providerName);
    return provider.embeddings(texts);
  }
}
