# AJA Configuration Guide

AJA is configured primarily through a validated JSON configuration file, environment variables, and the `AJA_DATA_DIR`.

## The Data Directory

All AJA state, memories (LanceDB), execution batons, and configurations are stored in the AJA Data Directory. 

By default, this resolves to the standard OS user data directory:
- **Windows**: `C:\Users\<User>\AppData\Local\Anixes\AJA`
- **macOS**: `~/Library/Application Support/AJA`
- **Linux**: `~/.local/share/AJA`

To override the data directory (useful for containerization or isolated environments), set the `AJA_DATA_DIR` environment variable:

```bash
export AJA_DATA_DIR=/var/lib/aja
```

## aja.json

The primary configuration file is `aja.json`, located inside the `AJA_DATA_DIR` (or the project root during legacy fallback).

It is strictly validated against a Pydantic schema (`aja.config_schema.AJAConfig`).

### Example `aja.json`
```json
{
  "system": {
    "log_level": "INFO",
    "telemetry_enabled": true,
    "direct_execution": false
  },
  "models": {
    "default_provider": "openai",
    "temperature": 0.3
  },
  "memory": {
    "lancedb_uri": "sqlite:///{AJA_DATA_DIR}/lancedb",
    "retention_days": 30
  }
}
```

## Security & API Keys

AJA relies on API keys for LLM providers and integrations. **Never hardcode keys in the repository.**

Instead, provide them via environment variables or a `.env` file located in the `AJA_DATA_DIR`:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`

If a `.env` file is present in the `AJA_DATA_DIR` or the current working directory, AJA will automatically load it on startup.
