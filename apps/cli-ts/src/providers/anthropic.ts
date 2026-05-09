/**
 * Anthropic Native Provider
 * 
 * Uses the Anthropic Messages API directly.
 * System prompt is a top-level field, not a message.
 * Tool use returns content blocks with type "tool_use".
 * Streaming uses typed SSE events.
 */

import type {
  IProvider, ChatMessage, ChatOptions, CompleteOptions,
  ChatResponse, StreamChunk, ToolCallResponse, ToolSchema, TokenUsage,
} from './types.js';
import { CostTracker } from './costTracker.js';

const ANTHROPIC_API = 'https://api.anthropic.com';
const ANTHROPIC_VERSION = '2023-06-01';

export interface AnthropicConfig {
  apiKey: string;
  defaultModel?: string;
  baseUrl?: string;
}

export class AnthropicProvider implements IProvider {
  readonly name = 'anthropic';
  readonly supportsStreaming = true;
  readonly supportsToolCalls = true;
  readonly supportsEmbeddings = false;

  private apiKey: string;
  private baseUrl: string;
  private defaultModel: string;
  private costTracker: CostTracker;

  constructor(config: AnthropicConfig, costTracker?: CostTracker) {
    this.apiKey = config.apiKey;
    this.baseUrl = (config.baseUrl || ANTHROPIC_API).replace(/\/+$/, '');
    this.defaultModel = config.defaultModel || 'claude-sonnet-4-20250514';
    this.costTracker = costTracker || new CostTracker();
  }

  async chat(messages: ChatMessage[], options?: ChatOptions): Promise<ChatResponse> {
    const model = options?.model || this.defaultModel;
    const { system, msgs } = this.splitMessages(messages, options?.systemPrompt);

    const body: Record<string, unknown> = {
      model, messages: msgs, max_tokens: options?.maxTokens || 4096,
    };
    if (system) body.system = system;
    if (options?.temperature != null) body.temperature = options.temperature;
    if (options?.topP) body.top_p = options.topP;
    if (options?.stop) body.stop_sequences = options.stop;

    const data = await this.post('/v1/messages', body, options?.abortSignal);
    const content = (data.content || []).filter((b: any) => b.type === 'text').map((b: any) => b.text).join('');
    const usage = this.extractUsage(data);
    this.costTracker.recordTurn(model, usage, this.name);

    return { content, usage, model: data.model || model,
      finishReason: data.stop_reason === 'tool_use' ? 'tool_calls' : data.stop_reason === 'end_turn' ? 'stop' : 'unknown' };
  }

  async complete(prompt: string, options?: CompleteOptions): Promise<string> {
    return (await this.chat([{ role: 'user', content: prompt }], { ...options })).content;
  }

  async *stream(messages: ChatMessage[], options?: ChatOptions): AsyncIterable<StreamChunk> {
    const model = options?.model || this.defaultModel;
    const { system, msgs } = this.splitMessages(messages, options?.systemPrompt);
    const body: Record<string, unknown> = { model, messages: msgs, max_tokens: options?.maxTokens || 4096, stream: true };
    if (system) body.system = system;
    if (options?.temperature != null) body.temperature = options.temperature;

    const response = await fetch(`${this.baseUrl}/v1/messages`, {
      method: 'POST', headers: this.headers(), body: JSON.stringify(body), signal: options?.abortSignal,
    });
    if (!response.ok) throw new Error(`[anthropic] Stream ${response.status}: ${await response.text()}`);

    const reader = response.body?.getReader();
    if (!reader) throw new Error('[anthropic] No stream body');
    const decoder = new TextDecoder();
    let buffer = '', evType = '', usg: Partial<TokenUsage> = {};

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n'); buffer = lines.pop() || '';
        for (const line of lines) {
          const t = line.trim();
          if (t.startsWith('event:')) { evType = t.slice(6).trim(); continue; }
          if (!t.startsWith('data:')) continue;
          try {
            const c = JSON.parse(t.slice(5).trim());
            if (evType === 'content_block_delta' && c.delta?.text) yield { delta: c.delta.text, done: false };
            if (evType === 'message_start' && c.message?.usage) usg.promptTokens = c.message.usage.input_tokens;
            if (evType === 'message_delta' && c.usage) usg.completionTokens = c.usage.output_tokens;
            if (evType === 'message_stop') { yield { delta: '', done: true, usage: { ...usg, totalTokens: (usg.promptTokens||0)+(usg.completionTokens||0) } }; return; }
          } catch {}
        }
      }
    } finally { reader.releaseLock(); }
    if (usg.promptTokens || usg.completionTokens) this.costTracker.recordTurn(model, { promptTokens: usg.promptTokens||0, completionTokens: usg.completionTokens||0, totalTokens: (usg.promptTokens||0)+(usg.completionTokens||0) }, this.name);
  }

  async toolCall(messages: ChatMessage[], tools: ToolSchema[], options?: ChatOptions): Promise<ToolCallResponse> {
    const model = options?.model || this.defaultModel;
    const { system, msgs } = this.splitMessages(messages, options?.systemPrompt);
    const aTools = tools.map(t => ({ name: t.function.name, description: t.function.description, input_schema: t.function.parameters }));
    const body: Record<string, unknown> = { model, messages: msgs, tools: aTools, max_tokens: options?.maxTokens || 4096 };
    if (system) body.system = system;
    if (options?.temperature != null) body.temperature = options.temperature;

    const data = await this.post('/v1/messages', body, options?.abortSignal);
    const content = (data.content||[]).filter((b:any) => b.type==='text').map((b:any) => b.text).join('');
    const toolCalls = (data.content||[]).filter((b:any) => b.type==='tool_use').map((b:any) => ({ id: b.id, name: b.name, input: b.input||{} }));
    const usage = this.extractUsage(data);
    this.costTracker.recordTurn(model, usage, this.name);
    return { content, usage, model: data.model||model, finishReason: data.stop_reason==='tool_use'?'tool_calls':'stop', toolCalls };
  }

  async embeddings(_texts: string[]): Promise<number[][]> {
    throw new Error('[anthropic] Embeddings not supported. Use OpenAI or a local model.');
  }

  // ── Helpers ─────────────────────────────────────────────────

  private splitMessages(messages: ChatMessage[], systemPrompt?: string) {
    let system = systemPrompt || '';
    const msgs: Array<Record<string, unknown>> = [];
    for (const m of messages) {
      if (m.role === 'system') { system = system ? `${system}\n\n${m.content}` : m.content; continue; }
      if (m.role === 'tool') { msgs.push({ role: 'user', content: [{ type: 'tool_result', tool_use_id: m.tool_call_id, content: m.content }] }); continue; }
      msgs.push({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content });
    }
    return { system, msgs };
  }

  private extractUsage(data: any): TokenUsage {
    return { promptTokens: data.usage?.input_tokens||0, completionTokens: data.usage?.output_tokens||0, totalTokens: (data.usage?.input_tokens||0)+(data.usage?.output_tokens||0) };
  }

  private headers(): Record<string, string> {
    return { 'Content-Type': 'application/json', 'x-api-key': this.apiKey, 'anthropic-version': ANTHROPIC_VERSION };
  }

  private async post(endpoint: string, body: unknown, signal?: AbortSignal): Promise<any> {
    const r = await fetch(`${this.baseUrl}${endpoint}`, { method: 'POST', headers: this.headers(), body: JSON.stringify(body), signal });
    if (!r.ok) throw new Error(`[anthropic] ${r.status}: ${await r.text()}`);
    return r.json();
  }
}
