/**
 * AgentX Universal Provider Types
 * 
 * Every LLM provider (OpenAI, Anthropic, Gemini, Ollama, etc.)
 * implements the IProvider interface for unified access.
 */

// ── Message Types ─────────────────────────────────────────────

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';

export interface ChatMessage {
  role: MessageRole;
  content: string;
  name?: string;
  /** For tool results sent back to the model */
  tool_call_id?: string;
}

export interface ToolCallMessage extends ChatMessage {
  role: 'assistant';
  content: string;
  tool_calls: ToolCallRequest[];
}

export interface ToolCallRequest {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string; // JSON string
  };
}

// ── Tool Schema ───────────────────────────────────────────────

export interface ToolSchema {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>; // JSON Schema
  };
}

// ── Options ───────────────────────────────────────────────────

export interface ChatOptions {
  model?: string;
  temperature?: number;
  maxTokens?: number;
  systemPrompt?: string;
  topP?: number;
  stop?: string[];
  abortSignal?: AbortSignal;
}

export interface CompleteOptions {
  model?: string;
  temperature?: number;
  maxTokens?: number;
  stop?: string[];
  abortSignal?: AbortSignal;
}

// ── Response Types ────────────────────────────────────────────

export interface TokenUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  /** Estimated cost in USD, based on known model pricing */
  estimatedCost?: number;
}

export interface ChatResponse {
  content: string;
  usage: TokenUsage;
  model: string;
  finishReason: 'stop' | 'tool_calls' | 'length' | 'content_filter' | 'unknown';
}

export interface StreamChunk {
  delta: string;
  usage?: Partial<TokenUsage>;
  done: boolean;
}

export interface ToolCallResponse extends ChatResponse {
  toolCalls: Array<{
    id: string;
    name: string;
    input: Record<string, unknown>;
  }>;
}

// ── Provider Interface ────────────────────────────────────────

export interface IProvider {
  readonly name: string;
  readonly supportsStreaming: boolean;
  readonly supportsToolCalls: boolean;
  readonly supportsEmbeddings: boolean;

  /**
   * Standard chat completion.
   */
  chat(messages: ChatMessage[], options?: ChatOptions): Promise<ChatResponse>;

  /**
   * Simple text completion (prompt in, text out).
   * Falls back to chat() with a single user message if not natively supported.
   */
  complete(prompt: string, options?: CompleteOptions): Promise<string>;

  /**
   * Streaming chat — yields incremental deltas.
   */
  stream(messages: ChatMessage[], options?: ChatOptions): AsyncIterable<StreamChunk>;

  /**
   * Chat with tool/function calling support.
   * Returns tool call requests the engine should execute.
   */
  toolCall(
    messages: ChatMessage[],
    tools: ToolSchema[],
    options?: ChatOptions
  ): Promise<ToolCallResponse>;

  /**
   * Generate embeddings for the given texts.
   * Returns an array of vectors (one per input text).
   */
  embeddings(texts: string[]): Promise<number[][]>;
}

// ── Provider Config ───────────────────────────────────────────

export interface ProviderConfig {
  name: string;
  type: 'openai-compat' | 'anthropic' | 'gemini';
  baseUrl: string;
  apiKey?: string;
  defaultModel?: string;
  headers?: Record<string, string>;
}

// ── Provider Info (for listing) ───────────────────────────────

export interface ProviderInfo {
  name: string;
  type: string;
  baseUrl: string;
  defaultModel?: string;
  connected: boolean;
}
