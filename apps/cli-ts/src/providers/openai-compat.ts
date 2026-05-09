/**
 * OpenAI-Compatible Provider
 * 
 * Handles: OpenAI, OpenRouter, Ollama, LM Studio, llama.cpp, vLLM
 * All use the same /v1/chat/completions endpoint with minor header differences.
 */

import type {
  IProvider,
  ChatMessage,
  ChatOptions,
  CompleteOptions,
  ChatResponse,
  StreamChunk,
  ToolCallResponse,
  ToolSchema,
  TokenUsage,
} from './types.js';
import { CostTracker } from './costTracker.js';

export interface OpenAICompatConfig {
  name: string;
  baseUrl: string;
  apiKey?: string;
  defaultModel?: string;
  headers?: Record<string, string>;
}

export class OpenAICompatProvider implements IProvider {
  readonly name: string;
  readonly supportsStreaming = true;
  readonly supportsToolCalls = true;
  readonly supportsEmbeddings = true;

  private baseUrl: string;
  private apiKey: string;
  private defaultModel: string;
  private extraHeaders: Record<string, string>;
  private costTracker: CostTracker;

  constructor(config: OpenAICompatConfig, costTracker?: CostTracker) {
    this.name = config.name;
    this.baseUrl = config.baseUrl.replace(/\/+$/, '');
    this.apiKey = config.apiKey || '';
    this.defaultModel = config.defaultModel || 'gpt-4o-mini';
    this.extraHeaders = config.headers || {};
    this.costTracker = costTracker || new CostTracker();
  }

  // ── Chat ────────────────────────────────────────────────────

  async chat(messages: ChatMessage[], options?: ChatOptions): Promise<ChatResponse> {
    const model = options?.model || this.defaultModel;
    const body: Record<string, unknown> = {
      model,
      messages: this.formatMessages(messages, options?.systemPrompt),
      temperature: options?.temperature ?? 0.7,
    };
    if (options?.maxTokens) body.max_tokens = options.maxTokens;
    if (options?.topP) body.top_p = options.topP;
    if (options?.stop) body.stop = options.stop;

    const data = await this.post('/chat/completions', body, options?.abortSignal);
    const choice = data.choices?.[0];

    const usage: TokenUsage = {
      promptTokens: data.usage?.prompt_tokens || 0,
      completionTokens: data.usage?.completion_tokens || 0,
      totalTokens: data.usage?.total_tokens || 0,
    };

    this.costTracker.recordTurn(model, usage, this.name);

    return {
      content: choice?.message?.content || '',
      usage,
      model: data.model || model,
      finishReason: this.mapFinishReason(choice?.finish_reason),
    };
  }

  // ── Complete ────────────────────────────────────────────────

  async complete(prompt: string, options?: CompleteOptions): Promise<string> {
    const resp = await this.chat(
      [{ role: 'user', content: prompt }],
      { ...options }
    );
    return resp.content;
  }

  // ── Stream ──────────────────────────────────────────────────

  async *stream(messages: ChatMessage[], options?: ChatOptions): AsyncIterable<StreamChunk> {
    const model = options?.model || this.defaultModel;
    const body: Record<string, unknown> = {
      model,
      messages: this.formatMessages(messages, options?.systemPrompt),
      temperature: options?.temperature ?? 0.7,
      stream: true,
      stream_options: { include_usage: true },
    };
    if (options?.maxTokens) body.max_tokens = options.maxTokens;
    if (options?.topP) body.top_p = options.topP;
    if (options?.stop) body.stop = options.stop;

    const response = await fetch(`${this.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: this.buildHeaders(),
      body: JSON.stringify(body),
      signal: options?.abortSignal,
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(`[${this.name}] Stream failed (${response.status}): ${err}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error(`[${this.name}] No stream body`);

    const decoder = new TextDecoder();
    let buffer = '';
    let totalUsage: Partial<TokenUsage> | undefined;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === '[DONE]') {
            yield { delta: '', done: true, usage: totalUsage };
            return;
          }

          try {
            const chunk = JSON.parse(payload);
            const delta = chunk.choices?.[0]?.delta?.content || '';
            if (chunk.usage) {
              totalUsage = {
                promptTokens: chunk.usage.prompt_tokens,
                completionTokens: chunk.usage.completion_tokens,
                totalTokens: chunk.usage.total_tokens,
              };
            }
            if (delta) {
              yield { delta, done: false };
            }
          } catch {
            // Skip malformed chunks
          }
        }
      }
    } finally {
      reader.releaseLock();
    }

    // Record cost for the full stream
    if (totalUsage?.totalTokens) {
      this.costTracker.recordTurn(model, totalUsage as TokenUsage, this.name);
    }
  }

  // ── Tool Call ───────────────────────────────────────────────

  async toolCall(
    messages: ChatMessage[],
    tools: ToolSchema[],
    options?: ChatOptions
  ): Promise<ToolCallResponse> {
    const model = options?.model || this.defaultModel;
    const body: Record<string, unknown> = {
      model,
      messages: this.formatMessages(messages, options?.systemPrompt),
      tools,
      tool_choice: 'auto',
      temperature: options?.temperature ?? 0.7,
    };
    if (options?.maxTokens) body.max_tokens = options.maxTokens;

    const data = await this.post('/chat/completions', body, options?.abortSignal);
    const choice = data.choices?.[0];

    const usage: TokenUsage = {
      promptTokens: data.usage?.prompt_tokens || 0,
      completionTokens: data.usage?.completion_tokens || 0,
      totalTokens: data.usage?.total_tokens || 0,
    };

    this.costTracker.recordTurn(model, usage, this.name);

    const toolCalls = (choice?.message?.tool_calls || []).map((tc: any) => ({
      id: tc.id,
      name: tc.function.name,
      input: JSON.parse(tc.function.arguments || '{}'),
    }));

    return {
      content: choice?.message?.content || '',
      usage,
      model: data.model || model,
      finishReason: this.mapFinishReason(choice?.finish_reason),
      toolCalls,
    };
  }

  // ── Embeddings ──────────────────────────────────────────────

  async embeddings(texts: string[]): Promise<number[][]> {
    const data = await this.post('/embeddings', {
      model: 'text-embedding-3-small',
      input: texts,
    });

    return (data.data || [])
      .sort((a: any, b: any) => a.index - b.index)
      .map((item: any) => item.embedding);
  }

  // ── Private Helpers ─────────────────────────────────────────

  private formatMessages(
    messages: ChatMessage[],
    systemPrompt?: string
  ): Array<Record<string, unknown>> {
    const formatted: Array<Record<string, unknown>> = [];

    if (systemPrompt) {
      formatted.push({ role: 'system', content: systemPrompt });
    }

    for (const msg of messages) {
      const entry: Record<string, unknown> = {
        role: msg.role,
        content: msg.content,
      };
      if (msg.tool_call_id) entry.tool_call_id = msg.tool_call_id;
      if (msg.name) entry.name = msg.name;
      formatted.push(entry);
    }

    return formatted;
  }

  private buildHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...this.extraHeaders,
    };
    if (this.apiKey) {
      headers['Authorization'] = `Bearer ${this.apiKey}`;
    }
    return headers;
  }

  private async post(endpoint: string, body: unknown, signal?: AbortSignal): Promise<any> {
    const url = `${this.baseUrl}${endpoint}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: this.buildHeaders(),
      body: JSON.stringify(body),
      signal,
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`[${this.name}] ${response.status}: ${errText}`);
    }

    return response.json();
  }

  private mapFinishReason(reason?: string): ChatResponse['finishReason'] {
    switch (reason) {
      case 'stop': return 'stop';
      case 'tool_calls': return 'tool_calls';
      case 'length': return 'length';
      case 'content_filter': return 'content_filter';
      default: return 'unknown';
    }
  }
}
