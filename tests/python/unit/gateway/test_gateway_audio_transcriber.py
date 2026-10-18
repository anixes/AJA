"""Unit tests for gateway audio transcription and local model locking."""

import threading
from unittest.mock import MagicMock, patch
import pytest

from aja.gateway.audio_transcriber import _get_local_whisper_model, _WHISPER_LOCK
import aja.gateway.audio_transcriber as transcriber_mod


def test_whisper_concurrency_lock():
    # Reset model to None to test lazy loading under lock
    orig_model = transcriber_mod._LOCAL_WHISPER_MODEL
    transcriber_mod._LOCAL_WHISPER_MODEL = None

    loaded_instances = []

    def mock_load_model(name):
        import time
        time.sleep(0.02)
        inst = MagicMock(name=f"whisper_{name}")
        loaded_instances.append(inst)
        return inst

    try:
        with patch.dict("sys.modules", {"whisper": MagicMock(load_model=mock_load_model)}):
            threads = []
            results = []

            def worker():
                m = _get_local_whisper_model()
                results.append(m)

            for _ in range(5):
                t = threading.Thread(target=worker)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Ensure all threads received the same model instance and load_model was called exactly once
            assert len(loaded_instances) == 1
            for r in results:
                assert r is loaded_instances[0]
    finally:
        transcriber_mod._LOCAL_WHISPER_MODEL = orig_model
