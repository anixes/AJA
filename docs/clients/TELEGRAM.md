# Telegram Client & Gateway Adapter

The AJA Telegram Gateway connects the autonomous runtime to Telegram, turning a Telegram bot into a fully interactive mobile terminal, assistant, and remote operator.

---

## 1. Key Features

### 1. Continuous In-App Typing Status Pulse
- In Telegram Bot API, calling `send_chat_action(chat_id, "typing")` expires automatically after 4–5 seconds.
- AJA's `continuous_chat_action(bot, chat_id, action="typing", interval=4.0)` runs a background pulse task that keeps the native **"AJA is typing..."** status indicator visible in the user's Telegram header throughout long multi-step reasoning, tool execution, and planning turns.
- Automatically and safely cancels upon turn completion, response delivery, or error.

### 2. Read Receipts & Visual Reactions
- **Immediate Read Receipt (`👀`)**: Immediately acknowledged on the incoming message via `_safe_set_reaction` as soon as the message is received by the orchestrator.
- **Completion Reaction (`✅` / `👍`)**: Swapped to `✅` when the turn successfully finishes and the reply or final `StatusBubble` is delivered (with automatic fallback to `👍` for chats that disallow custom emoji reactions).
- **Failure Reaction (`👎`)**: Automatically applied if an unhandled exception or critical error occurs during event processing.

### 3. Voice Notes & Audio Transcription
- Listens to incoming voice notes and audio files (`filters.VOICE | filters.AUDIO`).
- Downloads `.oga`, `.ogg`, `.mp3` audio files to `<DATA_DIR>/audio/voice_<message_id>.ogg`.
- Automatically transcribes audio through `aja.gateway.audio_transcriber.AudioTranscriber`:
  - **Google Gemini Multimodal Audio**: Uses `gemini-2.5-flash` with direct inline audio data.
  - **OpenAI Whisper API**: Calls `whisper-1` via official REST client.
  - **Local Whisper**: Fallback to locally installed `whisper` library if available.
- Injects formatted speech transcriptions (`🎙️ [Voice Note Transcript (Xs)]: "..."`) into the conversation prompt, allowing seamless voice-command operation.

### 4. Code & Document Ingestion
- Listens to document attachments (`filters.Document.ALL`).
- **Code & Text Files** (`.py`, `.json`, `.md`, `.txt`, `.csv`, `.log`, `.yaml`, etc.):
  - Automatically reads content using UTF-8/Latin-1 fallback decoding.
  - Saves a persistent copy to `<DATA_DIR>/uploads/<filename>`.
  - Injects formatted code blocks with syntax highlighting into the conversation prompt.
- **Uncompressed Images** (`.png`, `.jpg`, `.webp` sent as files):
  - Converts image bytes into base64 data URLs for VLM vision analysis.
- **Binary & Archive Files**:
  - Saves files locally and passes safe file descriptors and paths to the agent.

### 5. Stickers & Location Pins
- Automatically translates stickers into descriptive text tags (`[Sticker: <emoji>]`).
- Translates location coordinates into structured location markers (`[Location: latitude=..., longitude=...]`).

### 6. Mobile GPU / Local Model Remote Control
- `/local` or `/models`: Renders the host hardware profile (OS, CPU cores, RAM, NVIDIA GPU/VRAM) and all discovered `.gguf` models across mounted drives.
- Interactive 1-tap inline buttons (`[▶ Start ...]`, `[⏹ Stop ...]`, `[🔄 Rescan ...]`) formatted within Telegram's 64-byte callback token limit.
- Command-line equivalents: `/local start <model_name>`, `/local stop`.

---

## 2. Configuration

In your `.env` or environment variables:
```bash
# Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN="1234567890:AA..."

# Authorized user ID from @userinfobot (unauthorized users are rejected)
TELEGRAM_ALLOWED_USER_ID="987654321"

# Optional transcription API keys
GEMINI_API_KEY="..."
OPENAI_API_KEY="..."
```

---

## 3. Running the Gateway

Start the standalone gateway server:
```powershell
py -3.12 -m aja.gateway.server
```

Or start the unified supervised daemon:
```powershell
aja daemon start
```
