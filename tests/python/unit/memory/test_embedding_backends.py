"""Unit tests for embedding backend selection (aja.embeddings.service)."""

import pytest

from aja.config_schema import SwarmSettings
from aja.embeddings import service as emb


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("AJA_MOCK_EMBEDDINGS", raising=False)
    monkeypatch.delenv("AJA_EMBEDDING_BACKEND", raising=False)
    emb._reset_for_tests()
    yield
    emb._reset_for_tests()


class TestResolutionMatrix:
    def test_mock_env_beats_everything(self, monkeypatch):
        monkeypatch.setenv("AJA_MOCK_EMBEDDINGS", "1")
        monkeypatch.setenv("AJA_EMBEDDING_BACKEND", "sentence_transformers")
        assert emb.get_active_backend() == "mock"

    def test_env_beats_config(self, monkeypatch):
        monkeypatch.setattr(emb, "_read_config_backend", lambda: "onnx")
        monkeypatch.setenv("AJA_EMBEDDING_BACKEND", "mock")
        assert emb.get_active_backend() == "mock"

    def test_config_beats_auto(self, monkeypatch):
        monkeypatch.setattr(emb, "_read_config_backend", lambda: "sentence_transformers")
        # fastembed may or may not exist locally; config must win regardless.
        monkeypatch.setattr(emb, "_fastembed_available", lambda: True)
        assert emb.get_active_backend() == "sentence_transformers"

    def test_auto_prefers_onnx_when_fastembed_available(self, monkeypatch):
        monkeypatch.setattr(emb, "_read_config_backend", lambda: "auto")
        monkeypatch.setattr(emb, "_fastembed_available", lambda: True)
        assert emb.get_active_backend() == "onnx"

    def test_auto_falls_back_to_sentence_transformers(self, monkeypatch):
        monkeypatch.setattr(emb, "_read_config_backend", lambda: "auto")
        monkeypatch.setattr(emb, "_fastembed_available", lambda: False)
        assert emb.get_active_backend() == "sentence_transformers"

    def test_unknown_value_falls_back_to_auto(self, monkeypatch):
        monkeypatch.setenv("AJA_EMBEDDING_BACKEND", "brainwave")
        monkeypatch.setattr(emb, "_fastembed_available", lambda: False)
        assert emb.get_active_backend() == "sentence_transformers"


class TestConfigField:
    def test_swarm_settings_default_is_auto(self):
        assert SwarmSettings().embedding_backend == "auto"

    def test_explicit_field_accepted(self):
        assert SwarmSettings(embedding_backend="onnx").embedding_backend == "onnx"


class TestMockPath:
    def test_mock_vectors_unchanged_and_deterministic(self, monkeypatch):
        monkeypatch.setenv("AJA_MOCK_EMBEDDINGS", "1")
        svc = emb.EmbeddingService()
        v1 = svc.embed("hello world")
        v2 = svc.embed("hello world")
        assert v1 == v2
        assert len(v1) == 384
        # Unit-length normalization contract preserved
        mag = sum(x * x for x in v1) ** 0.5
        assert abs(mag - 1.0) < 1e-6

    def test_empty_text_zero_vector(self):
        assert emb.EmbeddingService().embed("") == [0.0] * 384

    def test_model_name_reports_mock(self, monkeypatch):
        monkeypatch.setenv("AJA_MOCK_EMBEDDINGS", "1")
        assert emb.EmbeddingService().get_model_name() == "mock-bag-of-words"


class TestSentenceTransformersSelection:
    def test_explicit_selection_loads_st_model(self, monkeypatch):
        """Explicit sentence_transformers selection dispatches to the ST loader."""
        monkeypatch.setenv("AJA_EMBEDDING_BACKEND", "sentence_transformers")
        loaded = {}

        class FakeST:
            def __init__(self, name):
                loaded["name"] = name

            def encode(self, text):
                return [0.5] * 384

        import types

        fake_mod = types.ModuleType("sentence_transformers")
        fake_mod.SentenceTransformer = FakeST
        monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", fake_mod)

        vecs = emb.EmbeddingService().embed("hi")
        assert len(vecs) == 384
        assert loaded["name"] == "all-MiniLM-L6-v2"
        assert emb.EmbeddingService().get_model_name() == "sentence-transformers/all-MiniLM-L6-v2"


class TestOnnxUnavailable:
    def test_explicit_onnx_without_runtime_raises_actionable_error(self, monkeypatch):
        monkeypatch.setenv("AJA_EMBEDDING_BACKEND", "onnx")

        import builtins

        real_import = builtins.__import__

        def no_fastembed(name, *a, **kw):
            if name == "fastembed":
                raise ImportError("No module named 'fastembed'")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", no_fastembed)
        with pytest.raises(RuntimeError, match=r"pip install.*vps|fastembed"):
            emb.EmbeddingService().embed("should fail loudly")

    def test_auto_never_raises_when_fastembed_missing(self, monkeypatch):
        monkeypatch.setattr(emb, "_read_config_backend", lambda: "auto")
        monkeypatch.setattr(emb, "_fastembed_available", lambda: False)
        assert emb.get_active_backend() == "sentence_transformers"
