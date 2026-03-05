"""Tests for output_cleaner."""

from tcd.output_cleaner import clean_output, dedup_lines, remove_markers, remove_noise_lines, strip_ansi


def test_strip_ansi_csi():
    text = "\x1b[31mhello\x1b[0m world"
    assert strip_ansi(text) == "hello world"


def test_strip_ansi_osc():
    text = "\x1b]0;title\x07content"
    assert strip_ansi(text) == "content"


def test_strip_ansi_cr():
    """CR simulates terminal overwriting: cursor returns to start, new text overwrites."""
    text = "line1\roverwrite"
    assert strip_ansi(text) == "overwrite"


def test_strip_ansi_clean_text_unchanged():
    text = "hello world\nno escape here"
    assert strip_ansi(text) == text


def test_remove_noise_lines():
    text = "real output\n   \nesc to interrupt\nmore output\n% context left: 80%"
    result = remove_noise_lines(text)
    assert "real output" in result
    assert "more output" in result
    assert "esc to interrupt" not in result
    assert "context left" not in result


def test_remove_markers():
    text = "TCD_REQ:abc123-1-123\nreal content\nTCD_DONE:abc123-1-123"
    result = remove_markers(text)
    assert "TCD_REQ" not in result
    assert "TCD_DONE" not in result
    assert "real content" in result


def test_dedup_lines():
    text = "a\na\nb\nb\nb\nc"
    assert dedup_lines(text) == "a\nb\nc"


def test_clean_output_full_pipeline():
    text = "\x1b[32mI did it!\x1b[0m\n\n\nesc to interrupt\nI did it!\n[tcd: session complete]"
    result = clean_output(text)
    assert result == "I did it!"
    assert "\x1b" not in result
