/**
 * Gemini Native Provider
 * 
 * Uses Google's GenerateContent API directly.
 * Key differences: system instruction field, function declarations, streaming via streamGenerateContent.
 */

import type {
  IProvider, ChatMessage, ChatOptions, CompleteOptions,
  ChatResponse, StreamChunk, ToolCallResponse, ToolSchema, TokenUsage,
} from './types.js';
import { CostTracker } from './costTracker.js';

const GEMINI_API = 'https://generativelanguage.googleapis.com';

export interface GeminiConfig {
  apiKey: string;
  defaultModel?: string;
  baseUrl?: string;
}

export class GeminiProvider implements IProvider {
  readonly name = 'gemini';
  readonly supportsStreaming = true;
  readonly supportsToolCalls = true;
  readonly supportsEmbeddings = true;

  private apiKey: string;
  private baseUrl: string;
  private defaultModel: string;
  private costTracker: CostTracker;

  constructor(config: GeminiConfig, costTracker?: CostTracker) {
    this.apiKey = config.apiKey;
    this.baseUrl = (config.baseUrl || GEMINI_API).replace(/\/+$/, '');
    this.defaultModel = config.defaultModel || 'gemini-2.5-flash';
    this.costTracker = costTracker || new CostTracker();
  }

  async chat(messages: ChatMessage[], options?: ChatOptions): Promise<ChatResponse> {
    const model = options?.model || this.defaultModel;
    const { systemInstruction, contents } = this.formatMessages(messages, options?.systemPrompt);

    const body: Record<string, unknown> = { contents };
    if (systemInstruction) body.systemInstruction = { parts: [{ text: systemInstruction }] };
    if (options?.temperature != null || options?.maxTokens || options?.topP) {
      body.generationConfig = {
        ...(options?.temperature != null && { temperature: options.temperature }),
        ...(options?.maxTokens && { maxOutputTokens: options.maxTokens }),
        ...(options?.topP && { topP: options.topP }),
        ...(options?.stop && { stopSequences: options.stop }),
      };
    }

    const data = await this.post(model, 'generateContent', body);
    const candidate = data.candidates?.[0];
    const text = candidate?.content?.parts?.map((p: any) => p.text || '').join('') || '';
    const usage = this.extractUsage(data);
    this.costTracker.recordTurn(model, usage, this.name);

    return { content: text, usage, model,
      finishReason: candidate?.finishReason === 'STOP' ? 'stop' :
                    candidate?.finishReason === 'MAX_TOKENS' ? 'length' :
                    candidate?.finishReason === 'SAFETY' ? 'content_filter' : 'unknown' };
  }

  async complete(prompt: string, options?: CompleteOptions): Promise<string> {
    return (await this.chat([{ role: 'user', content: prompt }], { ...options })).content;
  }

  async *stream(messages: ChatMessage[], options?: ChatOptions): AsyncIterable<StreamChunk> {
    const model = options?.model || this.defaultModel;
    const { systemInstruction, contents } = this.formatMessages(messages, options?.systemPrompt);

    const body: Record<string, unknown> = { contents };
    if (systemInstruction) body.systemInstruction = { parts: [{ text: systemInstruction }] };
    if (options?.temperature != null || options?.maxTokens) {
      body.generationConfig = {
        ...(options?.temperature != null && { temperature: options.temperature }),
        ...(options?.maxTokens && { maxOutputTokens: options.maxTokens }),
      };
    }

    const url = `${this.baseUrl}/v1beta/models/${model}:streamGenerateContent?alt=sse&key=${this.apiKey}`;
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: options?.abortSignal,
    });
    if (!response.ok) throw new Error(`[gemini] Stream ${response.status}: ${await response.text()}`);

    const reader = response.body?.getReader();
    if (!reader) throw new Error('[gemini] No stream body');
    const decoder = new TextDecoder();
    let buffer = '', finalUsage: Partial<TokenUsage> = {};

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n'); buffer = lines.pop() || '';
        for (const line of lines) {
          const t = line.trim();
          if (!t.startsWith('data:')) continue;
          try {
            const chunk = JSON.parse(t.slice(5).trim());
            const text = chunk.candidates?.[0]?.content?.parts?.map((p: any) => p.text || '').join('') || '';
            if (chunk.usageMetadata) {
              finalUsage = {
                promptTokens: chunk.usageMetadata.promptTokenCount || 0,
                completionTokens: chunk.usageMetadata.candidatesTokenCount || 0,
                totalTokens: chunk.usageMetadata.totalTokenCount || 0,
              };
            }
            if (text) yield { delta: text, done: false };
          } catch {}
        }
      }
    } finally { reader.releaseLock(); }

    yield { delta: '', done: true, usage: finalUsage };
    if (finalUsage.totalTokens) this.costTracker.recordTurn(model, finalUsage as TokenUsage, this.name);
  }

  async toolCall(messages: ChatMessage[], tools: ToolSchema[], options?: ChatOptions): Promise<ToolCallResponse> {
    const model = options?.model || this.defaultModel;
    const { systemInstruction, contents } = this.formatMessages(messages, options?.systemPrompt);

    const geminiTools = [{
      functionDeclarations: tools.map(t => ({
        name: t.function.name,
        description: t.function.description,
        parameters: t.function.parameters,
      })),
    }];

    const body: Record<string, unknown> = { contents, tools: geminiTools };
    if (systemInstruction) body.systemInstruction = { parts: [{ text: systemInstruction }] };
    if (options?.maxTokens) body.generationConfig = { maxOutputTokens: options.maxTokens };

    const data = await this.post(model, 'generateContent', body);
    const candidate = data.candidates?.[0];
    const parts = candidate?.content?.parts || [];

    const text = parts.filter((p: any) => p.text).map((p: any) => p.text).join('');
    const toolCalls = parts.filter((p: any) => p.functionCall).map((p: any, i: number) => ({
      id: `call_${i}`,
      name: p.functionCall.name,
      input: p.functionCall.args || {},
    }));

    const usage = this.extractUsage(data);
    this.costTracker.recordTurn(model, usage, this.name);
    return { content: text, usage, model, finishReason: toolCalls.length > 0 ? 'tool_calls' : 'stop', toolCalls };
  }

  async embeddings(texts: string[]): Promise<number[][]> {
    const results: number[][] = [];
    for (const text of texts) {
      const data = await this.post('text-embedding-004', 'embedContent', {
        content: { parts: [{ text }] },
      });
      results.push(data.embedding?.values || []);
    }
    return results;
  }

  // ── Helpers ─────────────────────────────────────────────────

  private formatMessages(messages: ChatMessage[], systemPrompt?: string) {
    let systemInstruction = systemPrompt || '';
    const contents: Array<Record<string, unknown>> = [];
    for (const m of messages) {
      if (m.role === 'system') { systemInstruction = systemInstruction ? `${systemInstruction}\n\n${m.content}` : m.content; continue; }
      if (m.role === 'tool') {
        contents.push({ role: 'function', parts: [{ functionResponse: { name: m.name || 'tool', response: { result: m.content } } }] });
        continue;
      }
      contents.push({ role: m.role === 'assistant' ? 'model' : 'user', parts: [{ text: m.content }] });
    }
    return { systemInstruction, contents };
  }

  private extractUsage(data: any): TokenUsage {
    const u = data.usageMetadata || {};
    return { promptTokens: u.promptTokenCount||0, completionTokens: u.candidatesTokenCount||0, totalTokens: u.totalTokenCount||0 };
  }

  private async post(model: string, method: string, body: unknown): Promise<any> {
    const url = `${this.baseUrl}/v1beta/models/${model}:${method}?key=${this.apiKey}`;
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`[gemini] ${r.status}: ${await r.text()}`);
    return r.json();
  }
}
