# External Model Providers (Free Tiers & Trials)

This document maps the landscape of external AI model providers that offer free API keys, trial credits, or significant free daily tiers as of 2026.

## 📊 Comparison Matrix

| Provider | Free Allowance | Rate Limits (Free Tier) | Key Models |
| :--- | :--- | :--- | :--- |
| **NVIDIA Build** | 1,000 - 5,000 Credits | Varies by NIM | Llama 3.3 Nemotron, Nemotron-4 |
| **Groq** | RPD-based (Daily) | 30 RPM (70B) / 14,400 RPD (8B) | Llama 3.3, Qwen 2.5, Mixtral |
| **Google AI Studio** | RPD-based (Daily) | 15 RPM (Flash) / 2 RPM (Pro) | Gemini 1.5 Pro/Flash |
| **Cloudflare** | 10k Neurons / Day | Quota-based | Llama 3.1, Mistral, Phi-3 |
| **DeepSeek** | 5 Million Tokens | 128K Context | DeepSeek-V4, DeepSeek-R1 |
| **Together AI** | $25.00 Trial | 200+ models serverless | Llama 4, Flux.1, Qwen |

---

## 🛠️ Integration Guide

### 1. Groq (The Speed Specialist)
Groq uses a custom LPU (Language Processing Unit) to achieve near-instant inference.
- **API Base**: `https://api.groq.com/openai/v1`
- **SDK**: `npm install groq-sdk` or `pip install groq`

```python
from groq import Groq
client = Groq(api_key="gsk_...")
# Uses OpenAI-style completions
```

### 2. Google AI Studio (The Context Specialist)
Gemini 1.5 Pro offers up to 2 million tokens of context, far exceeding any other free tier.
- **Portal**: [aistudio.google.com](https://aistudio.google.com)
- **SDK**: `@google/generative-ai`

### 3. OpenRouter (The Aggregator)
OpenRouter provides a unified API for hundreds of models and frequently features $0/token models from various providers.
- **API Base**: `https://openrouter.ai/api/v1`
- **Headers**: Requires `HTTP-Referer` and `X-Title`.

---

## 🔒 Security Note
When using external providers, remember:
1. **API Key Safety**: Never commit API keys to your repository. Use `.env` files and add them to `.gitignore`.
2. **Data Privacy**: Free tiers (especially Google AI Studio) may use your input data to improve their models unless you opt-out or use paid tiers.
3. **Claude Code Integration**: You can often use these as custom MCP servers or via environment variables if the CLI supports custom base URLs.

---
*Generated via RARV analysis on 2026-04-22.*
