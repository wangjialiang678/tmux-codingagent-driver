"""Tests for code absorbed from tmux-bridge.

Covers: UTF-8 chunking, CaptureDepth, DCS cleaning,
4-layer JSON extraction, and structured CodexOutput.
"""

from __future__ import annotations

import json

from tcd.output_cleaner import extract_json_payloads, strip_ansi
from tcd.providers.codex import CodexOutput, parse_codex_ndjson
from tcd.tmux_adapter import CaptureDepth, _utf8_chunks


# ---------------------------------------------------------------------------
# UTF-8 byte-level chunking
# ---------------------------------------------------------------------------


class TestUtf8Chunks:
    def test_ascii_under_limit(self):
        """Short ASCII text returns a single chunk."""
        chunks = _utf8_chunks("hello", 4096)
        assert chunks == ["hello"]

    def test_ascii_exact_limit(self):
        text = "A" * 4096
        chunks = _utf8_chunks(text, 4096)
        assert chunks == [text]

    def test_ascii_over_limit(self):
        text = "A" * 8192
        chunks = _utf8_chunks(text, 4096)
        assert len(chunks) == 2
        assert all(len(c.encode("utf-8")) <= 4096 for c in chunks)
        assert "".join(chunks) == text

    def test_cjk_not_split(self):
        """CJK characters (3 bytes each) should not be split mid-character."""
        # 1365 CJK chars = 4095 bytes, fits in one chunk
        text = "中" * 1365
        chunks = _utf8_chunks(text, 4096)
        assert chunks == [text]

        # 1366 CJK chars = 4098 bytes, needs 2 chunks
        text = "中" * 1366
        chunks = _utf8_chunks(text, 4096)
        assert len(chunks) == 2
        reconstructed = "".join(chunks)
        assert reconstructed == text

    def test_emoji_not_split(self):
        """4-byte emoji should not be split mid-character."""
        # Each emoji is 4 bytes
        text = "🚀" * 1025  # 4100 bytes
        chunks = _utf8_chunks(text, 4096)
        assert len(chunks) == 2
        assert "".join(chunks) == text

    def test_mixed_multibyte(self):
        """Mixed ASCII + CJK + emoji should chunk correctly."""
        text = "hello世界🎉" * 200
        chunks = _utf8_chunks(text, 4096)
        reconstructed = "".join(chunks)
        assert reconstructed == text
        for chunk in chunks:
            assert len(chunk.encode("utf-8")) <= 4096

    def test_empty_string(self):
        chunks = _utf8_chunks("", 4096)
        assert chunks == [""]


# ---------------------------------------------------------------------------
# CaptureDepth semantic constants
# ---------------------------------------------------------------------------


class TestCaptureDepth:
    def test_values(self):
        assert CaptureDepth.STATUS == 20
        assert CaptureDepth.HEALTH == 50
        assert CaptureDepth.CONTEXT == 500
        assert CaptureDepth.CHECKPOINT == 2000
        assert CaptureDepth.FULL == -1

    def test_is_int(self):
        """CaptureDepth values can be used as plain integers."""
        assert CaptureDepth.STATUS + 10 == 30


# ---------------------------------------------------------------------------
# DCS sequence cleaning
# ---------------------------------------------------------------------------


class TestDcsCleaning:
    def test_strip_dcs(self):
        text = "before\x1bPsome DCS data\x1b\\after"
        assert strip_ansi(text) == "beforeafter"

    def test_strip_dcs_with_csi(self):
        text = "\x1b[31mred\x1b[0m \x1bPdcs\x1b\\ clean"
        assert strip_ansi(text) == "red  clean"

    def test_cr_overwrite(self):
        """Carriage return simulates line overwriting."""
        text = "old text\rnew text"
        assert strip_ansi(text) == "new text"

    def test_cr_before_newline_preserved(self):
        """\\r\\n (Windows line ending) should not lose the newline."""
        text = "line1\r\nline2"
        result = strip_ansi(text)
        assert "line1" in result
        assert "line2" in result


# ---------------------------------------------------------------------------
# 4-layer JSON extraction
# ---------------------------------------------------------------------------


class TestExtractJsonPayloads:
    def test_embedded_json(self):
        """Strategy 1: raw_decode finds JSON embedded in text."""
        text = 'Some text {"key": "value"} more text'
        payloads = extract_json_payloads(text)
        assert len(payloads) == 1
        assert payloads[0] == {"key": "value"}

    def test_markdown_code_block(self):
        """Strategy 2: JSON in markdown code blocks."""
        text = '```json\n{"name": "test"}\n```'
        payloads = extract_json_payloads(text)
        assert any(p.get("name") == "test" for p in payloads)

    def test_ndjson_lines(self):
        """Strategy 3: line-by-line JSON parsing."""
        text = '{"a": 1}\nnot json\n{"b": 2}'
        payloads = extract_json_payloads(text)
        assert {"a": 1} in payloads
        assert {"b": 2} in payloads

    def test_json_in_json_string(self):
        """Strategy 4: recursive unwrap of JSON-encoded string values."""
        inner = json.dumps({"nested": True})
        outer = json.dumps({"data": inner})
        payloads = extract_json_payloads(outer)
        assert any(p.get("nested") is True for p in payloads)

    def test_dedup(self):
        """Duplicate objects should be deduplicated."""
        text = '{"x": 1} {"x": 1}'
        payloads = extract_json_payloads(text)
        assert len(payloads) == 1

    def test_empty_text(self):
        assert extract_json_payloads("") == []

    def test_no_json(self):
        assert extract_json_payloads("just plain text, no json here") == []

    def test_array_of_objects(self):
        """Arrays containing objects should extract each object."""
        text = '[{"a": 1}, {"b": 2}]'
        payloads = extract_json_payloads(text)
        assert {"a": 1} in payloads
        assert {"b": 2} in payloads


# ---------------------------------------------------------------------------
# Structured CodexOutput parsing
# ---------------------------------------------------------------------------


class TestCodexOutput:
    def test_thread_started(self):
        events = [
            {"type": "thread.started", "thread_id": "abc-123"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello"}},
        ]
        text = "\n".join(json.dumps(e) for e in events)
        output = parse_codex_ndjson(text)
        assert output.thread_id == "abc-123"
        assert output.agent_messages == ["Hello"]
        assert output.summary == "Hello"

    def test_files_modified(self):
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": json.dumps({"file": "src/main.py"}),
                },
            },
        ]
        text = "\n".join(json.dumps(e) for e in events)
        output = parse_codex_ndjson(text)
        assert output.files_modified == ["src/main.py"]

    def test_token_count(self):
        events = [
            {
                "type": "event_msg",
                "token_count": {"input_tokens": 100, "output_tokens": 50},
            },
        ]
        text = "\n".join(json.dumps(e) for e in events)
        output = parse_codex_ndjson(text)
        assert output.tokens == {"input": 100, "output": 50}

    def test_empty_input(self):
        output = parse_codex_ndjson("")
        assert output.thread_id is None
        assert output.agent_messages == []
        assert output.summary == ""

    def test_invalid_json_lines_skipped(self):
        text = "not json\n{bad json\n" + json.dumps({"type": "message", "content": "ok"})
        output = parse_codex_ndjson(text)
        assert output.agent_messages == ["ok"]

    def test_summary_truncation(self):
        long_msg = "x" * 1000
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": long_msg}}]
        text = "\n".join(json.dumps(e) for e in events)
        output = parse_codex_ndjson(text)
        assert len(output.summary) == 500

    def test_dedup_files(self):
        """Same file patched twice should appear once in files_modified."""
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": json.dumps({"file": "a.py"}),
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": json.dumps({"file": "a.py"}),
                },
            },
        ]
        text = "\n".join(json.dumps(e) for e in events)
        output = parse_codex_ndjson(text)
        assert output.files_modified == ["a.py"]
