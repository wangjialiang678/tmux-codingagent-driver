"""Tests for marker_detector."""

from tcd.marker_detector import (
    build_marker_prompt,
    extract_done_req_id,
    scan_for_context_limit,
    scan_for_marker,
)


def test_build_marker_prompt():
    result = build_marker_prompt("do something", "job1-0-123")
    assert "TCD_REQ:job1-0-123" in result
    assert "do something" in result
    assert "TCD_DONE:job1-0-123" in result


def test_scan_for_marker_found():
    text = "some output\nmore output\nTCD_DONE:abc-0-999\n"
    assert scan_for_marker(text, "abc-0-999") is True


def test_scan_for_marker_not_found():
    text = "some output\nmore output\n"
    assert scan_for_marker(text, "abc-0-999") is False


def test_scan_for_marker_wrong_id():
    text = "TCD_DONE:other-id\n"
    assert scan_for_marker(text, "abc-0-999") is False


def test_scan_for_marker_in_tail():
    """Marker in last 50 lines is found."""
    lines = [f"line {i}" for i in range(100)]
    lines.append("TCD_DONE:req-123")
    text = "\n".join(lines)
    assert scan_for_marker(text, "req-123") is True


def test_scan_for_marker_prefix_strict_match():
    text = "TCD_DONE:job1-1-12345\n"
    assert scan_for_marker(text, "job1-1-") is True


def test_scan_for_marker_prefix_avoids_partial_turn_match():
    text = "TCD_DONE:job1-10-12345\n"
    assert scan_for_marker(text, "job1-1-") is False


def test_scan_for_context_limit():
    assert scan_for_context_limit("Error: context window is full") is True
    assert scan_for_context_limit("token limit exceeded") is True
    assert scan_for_context_limit("everything is fine") is False


def test_extract_done_req_id():
    text = "output\nTCD_DONE:job1-2-456\nmore\n"
    assert extract_done_req_id(text) == "job1-2-456"


def test_extract_done_req_id_none():
    assert extract_done_req_id("no markers here") is None


def test_extract_done_req_id_last():
    """Multiple markers — returns the last one."""
    text = "TCD_DONE:first\nstuff\nTCD_DONE:second\n"
    assert extract_done_req_id(text) == "second"
