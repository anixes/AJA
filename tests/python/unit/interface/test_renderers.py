"""Tests for aja.interface.renderers (Rich event rendering pipeline)."""
from __future__ import annotations

import io

import pytest
from rich.console import Console

from aja.core.events import (
    ApprovalRequested,
    Delta,
    Error,
    Final,
    ToolFinished,
    ToolStarted,
)
from aja.interface.renderers import EventRenderer


def make_renderer() -> tuple[EventRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100, legacy_windows=False)
    return EventRenderer(console=console), buf


def strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


class TestRenderDelta:
    def test_prints_inline_without_newline(self):
        r, buf = make_renderer()
        r.render_delta("Hello ")
        r.render_delta("world")
        assert "Hello world" in buf.getvalue()

    def test_accumulates_into_buffer(self):
        r, _ = make_renderer()
        r.render_delta("foo")
        r.render_delta("bar")
        assert r.delta_buffer == "foobar"

    def test_reset_stream_clears_buffer(self):
        r, _ = make_renderer()
        r.render_delta("stale")
        r.reset_stream()
        assert r.delta_buffer == ""

    def test_markup_in_text_is_escaped(self):
        r, buf = make_renderer()
        r.render_delta("[bold red]not markup[/]")
        out = buf.getvalue()
        assert "[bold red]" in out  # literal text preserved, not consumed


class TestToolRendering:
    def test_tool_started_shows_name(self):
        r, buf = make_renderer()
        r.render_tool_started("search_web", "query=pandas")
        out = strip_ansi(buf.getvalue())
        assert "search_web" in out
        assert "query=pandas" in out

    def test_tool_started_args_redacted(self):
        r, buf = make_renderer()
        r.render_tool_started("fetch_url", "token=supersecret12345")
        out = strip_ansi(buf.getvalue())
        assert "supersecret12345" not in out
        assert "***REDACTED***" in out

    def test_tool_finished_success(self):
        r, buf = make_renderer()
        r.render_tool_finished("search_web", success=True, duration_ms=123.4)
        out = strip_ansi(buf.getvalue())
        assert "✓" in out
        assert "search_web" in out
        assert "123ms" in out

    def test_tool_finished_failure(self):
        r, buf = make_renderer()
        r.render_tool_finished("fetch_url", success=False, duration_ms=50.0)
        out = strip_ansi(buf.getvalue())
        assert "✗" in out
        assert "fetch_url" in out


class TestApproval:
    def test_returns_panel_with_reason_and_id(self):
        r, buf = make_renderer()
        panel = r.render_approval("ap-001", "runs rm -rf build", command="rm -rf build")
        assert panel is not None
        # Re-render into our own buffer to inspect content
        r2, buf2 = make_renderer()
        r2.console.print(
            r.render_approval("ap-001", "destructive cleanup", command="rm -rf /tmp/x")
        )
        out = strip_ansi(buf2.getvalue())
        assert "ap-001" in out
        assert "destructive cleanup" in out
        assert "rm -rf /tmp/x" in out
        assert "Approval Required" in out

    def test_await_approval_blocks_on_input(self, monkeypatch):
        r, buf = make_renderer()
        prompts: list[str] = []

        def fake_input(prompt=""):
            prompts.append(prompt)
            return "y"

        monkeypatch.setattr("builtins.input", fake_input)
        assert r.await_approval("ap-9", "because") is True
        assert prompts and "Approve?" in prompts[0]
        out = strip_ansi(buf.getvalue())
        assert "ap-9" in out and "because" in out

    def test_await_approval_rejects_non_yes(self, monkeypatch):
        r, _ = make_renderer()
        monkeypatch.setattr("builtins.input", lambda *a: "nope")
        assert r.await_approval("ap-10", "reason") is False

    def test_await_approval_eof_denies(self, monkeypatch):
        r, _ = make_renderer()

        def raise_eof(*a):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert r.await_approval("ap-11", "reason") is False


class TestError:
    def test_recoverable_error_panel(self):
        r, buf = make_renderer()
        panel = r.render_error("TIMEOUT", "tool took too long", recoverable=True)
        r.console.print(panel)
        out = strip_ansi(buf.getvalue())
        assert "TIMEOUT" in out
        assert "tool took too long" in out
        assert "RECOVERABLE" in out

    def test_fatal_error_severity_differs(self):
        r, buf = make_renderer()
        panel = r.render_error("PANIC", "boom", recoverable=False)
        r.console.print(panel)
        out = strip_ansi(buf.getvalue())
        assert "FATAL" in out
        assert "RECOVERABLE" not in out

    def test_message_markup_escaped(self):
        r, buf = make_renderer()
        r.console.print(r.render_error("X", "[red]injection[/]"))
        out = strip_ansi(buf.getvalue())
        assert "[red]" in out


class TestFinal:
    def test_returns_markdown_renderable(self):
        r, buf = make_renderer()
        md = r.render_final("# Heading\n\n- item")
        from rich.markdown import Markdown

        assert isinstance(md, Markdown)
        r.console.print(md)
        assert "Heading" in strip_ansi(buf.getvalue())

    def test_final_redacts_secrets(self):
        r, buf = make_renderer()
        md = r.render_final("key is sk-abcdefghijklmnop1234 ok")
        r.console.print(md)
        out = strip_ansi(buf.getvalue())
        assert "sk-abcdefghijklmnop1234" not in out


def _make_async(events):
    class _Stream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for e in events:
                yield e

    return _Stream()


class TestStreamEvents:
    @pytest.mark.anyio
    async def test_full_stream_renders_and_returns_final(self):
        r, buf = make_renderer()
        events = [
            Delta("Hi "),
            Delta("there"),
            ToolStarted("search_web", "q=python"),
            ToolFinished("search_web", True, 42.5),
            Final("## Done"),
        ]
        final = await r.stream_events(_make_async(events))
        assert final == "## Done"
        out = strip_ansi(buf.getvalue())
        assert "Hi there" in out
        assert "✓ search_web" in out
        assert "42ms" in out

    @pytest.mark.anyio
    async def test_stream_without_final_returns_none(self):
        r, _ = make_renderer()
        events = [Delta("partial")]
        assert await r.stream_events(_make_async(events)) is None

    @pytest.mark.anyio
    async def test_stream_dispatches_error_and_approval(self):
        r, buf = make_renderer()
        events = [
            ApprovalRequested("ap-x", "needs blessing"),
            Error("RATE_LIMIT", "slow down", recoverable=True),
            Final("ok"),
        ]
        final = await r.stream_events(_make_async(events))
        assert final == "ok"
        out = strip_ansi(buf.getvalue())
        assert "ap-x" in out
        assert "RATE_LIMIT" in out
        assert "RECOVERABLE" in out
