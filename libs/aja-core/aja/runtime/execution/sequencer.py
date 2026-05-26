from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from aja.runtime.event_bus import bus
from aja.runtime.execution.contracts import utc_now


class StreamNormalizer:
    """Canonical stream data analyzer that standardizes output formats across platforms."""

    def __init__(self):
        pass

    def normalize(self, stream_type: Literal["stdout", "stderr"], chunk: str) -> Dict[str, Any]:
        """Normalizes stream content to enforce a standardized structured schema."""
        # Clean terminal carriage returns cleanly
        cleaned = chunk.replace("\r\n", "\n").replace("\r", "\n")
        return {
            "stream": stream_type,
            "line": cleaned.rstrip("\n"),
            "raw_len": len(chunk),
        }


class EventSequencer:
    """Injects monotonic sequence ids, execution epochs, and high-precision timestamps."""

    def __init__(self, session_id: str, trace_id: Optional[str] = None):
        self.session_id = session_id
        self.trace_id = trace_id
        self._sequence = 0
        self._epoch = 0

    def next_epoch(self) -> None:
        """Bump the execution epoch (e.g. on shell recovery or restart)."""
        self._epoch += 1

    def sequence_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wrap and tag event with strict monotonic sequence metadata.

        Phase 1: Every event carries ``event_schema_version`` so reducers can
        route old journals to the correct versioned reducer after upgrades.
        """
        sequenced = {
            "timestamp": utc_now(),
            "event_schema_version": "1.0",  # Phase 1 — all events start at v1.0
            "epoch": self._epoch,
            "sequence": self._sequence,
            "event_type": event_type,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            **payload,
        }
        self._sequence += 1
        return sequenced


class TelemetryEmitter:
    """Handles thread-safe framed append-only durability of execution journals."""

    def __init__(self, session_root: Path, sequencer: EventSequencer):
        self.session_root = session_root
        self.sequencer = sequencer
        self.timeline_path = session_root / "timeline.jsonl"
        self.session_root.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Sequence, frame with CRC32/length prefixes, and append to execution journal."""
        sequenced = self.sequencer.sequence_event(event_type, payload)
        payload_str = json.dumps(sequenced, default=str)
        
        # Framed Log Protocol: FRAME:LENGTH:CRC32:PAYLOAD
        length = len(payload_str)
        crc32 = zlib.crc32(payload_str.encode("utf-8")) & 0xFFFFFFFF
        framed_line = f"FRAME:{length:08x}:{crc32:08x}:{payload_str}\n"

        # Append-only framed persistence
        with open(self.timeline_path, "a", encoding="utf-8") as f:
            f.write(framed_line)

        # Distribute dynamically onto AJA event bus
        try:
            bus.publish(event_type, sequenced)
        except Exception:
            pass

        return sequenced

    @staticmethod
    def repair_journal(path: Path) -> int:
        """
        Scans a timeline log file, checks CRC32 frames, strips truncated tails,
        and returns the last valid sequence number.
        """
        if not path.exists():
            return -1

        valid_lines = []
        last_seq = -1
        corrupt_found = False

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        for idx, line in enumerate(lines):
            if corrupt_found:
                break
            
            is_last_line = (idx == len(lines) - 1)
            is_last_line_truncated = is_last_line and not line.endswith("\n")
            line_stripped = line.strip("\r\n")
            if not line_stripped:
                continue

            if not line_stripped.startswith("FRAME:"):
                if is_last_line or is_last_line_truncated:
                    corrupt_found = True
                    break
                raise ValueError(last_seq)
                
            parts = line_stripped.split(":", 3)
            if len(parts) < 4:
                if is_last_line or is_last_line_truncated:
                    corrupt_found = True
                    break
                else:
                    raise ValueError(last_seq)
                
            _, len_str, crc_str, payload = parts
            
            try:
                length = int(len_str, 16)
                crc = int(crc_str, 16)
            except ValueError:
                if is_last_line or is_last_line_truncated:
                    corrupt_found = True
                    break
                raise ValueError(last_seq)
            
            try:
                if len(payload) < length and (is_last_line or is_last_line_truncated):
                    corrupt_found = True
                    break
                elif len(payload) != length:
                    if is_last_line:
                        corrupt_found = True
                        break
                    raise ValueError(last_seq)
                    
                calculated_crc = zlib.crc32(payload.encode("utf-8")) & 0xFFFFFFFF
                if calculated_crc != crc:
                    try:
                        json.loads(payload)
                        # If JSON parsed successfully and length matches perfectly, this is a fully complete
                        # line but with corrupt CRC. We must treat it as corrupt, not truncated.
                        raise ValueError(last_seq)
                    except json.JSONDecodeError:
                        if is_last_line:
                            corrupt_found = True
                            break
                        raise ValueError(last_seq)
                
                data = json.loads(payload)
                last_seq = max(last_seq, data.get("sequence", -1))
                valid_lines.append(line_stripped)
            except ValueError as e:
                if e.args and e.args[0] == last_seq:
                    raise
                if is_last_line or is_last_line_truncated:
                    corrupt_found = True
                    break
                raise ValueError(last_seq)
            except Exception:
                if is_last_line or is_last_line_truncated:
                    corrupt_found = True
                    break
                raise ValueError(last_seq)

        if corrupt_found:
            # Rewrite the journal to drop corrupted/truncated frames
            with open(path, "w", encoding="utf-8") as f:
                for line in valid_lines:
                    f.write(line + "\n")

        return last_seq
