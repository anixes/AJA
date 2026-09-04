"""
aja.gateway.telegram_local — Interactive Local Model Management for Telegram.
=============================================================================
Provides host hardware detection display, multi-drive GGUF model listing,
and 1-tap Telegram inline keyboard buttons for starting, stopping, and
switching local GPU inference engines without leaving Telegram.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from aja.models.local_manager import HostHardwareProfile, LocalModelInfo, LocalModelManager

logger = logging.getLogger(__name__)


def format_short_model_name(filename: str) -> str:
    """Create a friendly, compact label suitable for mobile Telegram buttons."""
    clean = re.sub(r"\.gguf$", "", filename, flags=re.IGNORECASE)
    clean = re.sub(r"[-_](it|instruct|q[0-9]_[k0-9_msal]+|f16|f32|q8_0)+", "", clean, flags=re.IGNORECASE)
    clean = clean.replace("-", " ").replace("_", " ").strip()
    if len(clean) > 24:
        clean = clean[:22] + ".."
    return clean.title()


def build_local_models_card(chat_id: Optional[str] = None) -> Tuple[str, Any]:
    """
    Construct the markdown status message and Telegram InlineKeyboardMarkup.
    Uses compact callback_data tokens ('ls:<idx>', 'lstp', 'lref', 'luse:<idx>')
    to strictly guarantee compliance with Telegram's 64-byte payload limit.
    """
    hw: HostHardwareProfile = LocalModelManager.get_hardware_profile()
    engines = LocalModelManager.probe_engines()
    active_models = LocalModelManager.get_active_model()
    disk_models: List[LocalModelInfo] = LocalModelManager.scan_disk_gguf_models()

    llama_status = engines.get("llama_cpp")
    llama_running = bool(llama_status and llama_status.running)

    ollama_status = engines.get("ollama")
    ollama_running = bool(ollama_status and ollama_status.running)

    # 1. Header & Host Hardware Profile
    lines = [
        "🖥️ **AJA Host Hardware & Local Models**\n",
        "**Host System**:",
        f"• **OS**: {hw.os_name} {hw.os_release} ({hw.cpu_cores} vCPUs)",
        f"• **RAM**: {hw.ram_total_gb:.1f} GB ({hw.ram_available_gb:.1f} GB available)",
    ]

    if hw.gpu_name:
        vram_str = f"{hw.vram_total_mb / 1024:.1f} GB" if hw.vram_total_mb else "N/A"
        free_str = f" ({hw.vram_free_mb / 1024:.1f} GB free)" if hw.vram_free_mb else ""
        driver_str = f" · Driver {hw.driver_version}" if hw.driver_version else ""
        lines.append(f"• **GPU**: {hw.gpu_name} ({vram_str} VRAM{free_str}{driver_str})")
    else:
        lines.append("• **GPU**: No dedicated NVIDIA GPU detected (CPU mode)")

    # 2. Runtimes & Active Roles
    lines.append("\n**Local Runtimes**:")
    if llama_running:
        lines.append("• **llama.cpp**: 🟢 **Running** on port 8080")
    else:
        server_bin = LocalModelManager.find_llama_server_binary()
        bin_note = "ready" if server_bin else "binary not found"
        lines.append(f"• **llama.cpp**: ⚪ Offline ({bin_note})")

    if ollama_running:
        lines.append(f"• **Ollama**: 🟢 Running on port 11434 ({ollama_status.models_count} models)")
    else:
        lines.append("• **Ollama**: ⚪ Offline")

    lines.append("\n**Active Agent Roles**:")
    lines.append(f"• 🧠 **Planner**: `{active_models.get('planner', 'cloud')}`")
    lines.append(f"• ⚡ **Worker**: `{active_models.get('worker', 'cloud')}`")

    # 3. Discovered Local Models
    if not disk_models:
        lines.append("\n📁 **Local Models**: No `.gguf` files detected in host model directories.")
        lines.append("_Drop `.gguf` models into `E:\\Models` or `~/.cache/lm-studio/models` to use local CUDA execution._")
    else:
        lines.append(f"\n📁 **Discovered Local GGUFs** ({len(disk_models)} ready on host):")
        for i, m in enumerate(disk_models[:6], start=1):
            rec_badge = f"\n   ↳ _{m.recommendation}_" if m.recommendation else ""
            lines.append(f"**{i}.** `{m.name}` ({m.size_gb:.2f} GB){rec_badge}")

        if len(disk_models) > 6:
            lines.append(f"_...and {len(disk_models) - 6} more models._")

    lines.append("\n_Tap an option below to start or switch models on your GPU:_")
    message_text = "\n".join(lines)

    # 4. Construct Telegram InlineKeyboardMarkup
    reply_markup = None
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard: List[List[InlineKeyboardButton]] = []

        # Start buttons for top models (up to 4 to keep mobile UI clean)
        start_row: List[InlineKeyboardButton] = []
        for idx, m in enumerate(disk_models[:4]):
            short_lbl = format_short_model_name(m.name)
            btn = InlineKeyboardButton(
                text=f"▶ {short_lbl}",
                callback_data=f"ls:{idx}",
            )
            start_row.append(btn)
            if len(start_row) == 2:
                keyboard.append(start_row)
                start_row = []
        if start_row:
            keyboard.append(start_row)

        # Active engine control row (if llama-server is active)
        if llama_running:
            active_worker = active_models.get("worker", "")
            ctl_row = [
                InlineKeyboardButton("⏹ Stop llama-server", callback_data="lstp"),
            ]
            if not active_worker.startswith("llama_cpp:"):
                ctl_row.append(InlineKeyboardButton("⚡ Route to Local", callback_data="luse:0"))
            keyboard.append(ctl_row)

        # Refresh & Doctor row
        keyboard.append([
            InlineKeyboardButton("🔄 Rescan Host", callback_data="lref"),
            InlineKeyboardButton("🩺 Doctor", callback_data="lstat"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
    except Exception as e:
        logger.debug("Failed to build Telegram InlineKeyboardMarkup: %s", e)
        reply_markup = None

    return message_text, reply_markup


async def handle_local_model_callback(
    data: str,
    callback_user_id: str,
    chat_id: str,
) -> Tuple[bool, str, Any]:
    """
    Process Telegram callback queries for local model management.
    Returns (authorized, status_message, updated_reply_markup).
    """
    allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID") or getattr(
        __import__("aja.config", fromlist=["TELEGRAM_ALLOWED_USER_ID"]),
        "TELEGRAM_ALLOWED_USER_ID",
        "",
    )
    if (
        not allowed_user_id
        or str(allowed_user_id).strip() in ("", "*")
        or callback_user_id != str(allowed_user_id).strip()
    ):
        logger.critical("Security Alert: unauthorized local model callback attempt by %s", callback_user_id)
        return False, "🚫 Unauthorized callback action.", None

    disk_models = LocalModelManager.scan_disk_gguf_models()

    # 1. Stop llama-server
    if data in ("lstp", "local_stop"):
        ok, msg = LocalModelManager.stop_llama_server()
        text, markup = build_local_models_card(chat_id)
        status_banner = f"⏹ **llama-server Stopped** ({msg})\n\n" if ok else f"⚠️ **Stop Notice**: {msg}\n\n"
        return True, status_banner + text, markup

    # 2. Refresh host models
    if data in ("lref", "lstat", "local_refresh"):
        text, markup = build_local_models_card(chat_id)
        return True, "🔄 **Host Rescanned**:\n\n" + text, markup

    # 3. Start model by index or name
    if data.startswith("ls:") or data.startswith("local_start:"):
        prefix = "ls:" if data.startswith("ls:") else "local_start:"
        target = data[len(prefix):]
        selected_model: Optional[LocalModelInfo] = None

        if target.isdigit():
            idx = int(target)
            if 0 <= idx < len(disk_models):
                selected_model = disk_models[idx]
        else:
            selected_model = next((m for m in disk_models if m.name == target), None)

        if not selected_model:
            text, markup = build_local_models_card(chat_id)
            return True, f"⚠️ Model index '{target}' not found on host.\n\n" + text, markup

        ok, msg = LocalModelManager.start_llama_server(
            selected_model.name,
            ngl=selected_model.auto_tuned_ngl,
        )
        if ok:
            LocalModelManager.activate_model(f"llama_cpp:{selected_model.name}", role="worker")
            text, markup = build_local_models_card(chat_id)
            confirm_banner = (
                f"✅ **Local Engine Started & Activated!**\n"
                f"• Model: `{selected_model.name}`\n"
                f"• Backend: llama.cpp CUDA (port 8080)\n"
                f"• GPU Layers: `-ngl {selected_model.auto_tuned_ngl or 99}`\n"
                f"• Role: Active Worker (Hybrid Mode)\n\n"
            )
            return True, confirm_banner + text, markup
        else:
            text, markup = build_local_models_card(chat_id)
            return True, f"❌ **Failed to Start Model**: {msg}\n\n" + text, markup

    # 4. Activate local model without restarting server
    if data.startswith("luse:"):
        target = data[5:]
        selected_model = None
        if target.isdigit():
            idx = int(target)
            if 0 <= idx < len(disk_models):
                selected_model = disk_models[idx]
        else:
            selected_model = next((m for m in disk_models if m.name == target), None)

        if selected_model:
            LocalModelManager.activate_model(f"llama_cpp:{selected_model.name}", role="worker")
            text, markup = build_local_models_card(chat_id)
            return True, f"⚡ **Active Worker Set to**: `{selected_model.name}`\n\n" + text, markup

    # Default fallback
    text, markup = build_local_models_card(chat_id)
    return True, text, markup
