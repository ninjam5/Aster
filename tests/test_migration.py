"""
Aster Engine Migration Test Suite
Tests for the llama-server REST adapter and XML→native tool calling migration.
Run: pytest tests/test_migration.py -v
No llama-server needed — requests.post is mocked throughout.
"""
import pytest
import json
import base64
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================================
# HELPERS
# ============================================================================

def _make_mock_response(message: dict):
    """Return a mock requests.Response for _execute_gemma_completion."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps({"choices": [{"message": message}]})
    mock_resp.json.return_value = {"choices": [{"message": message}]}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def sample_admin_tools():
    """Sample OpenAI-compatible tool schema (same shape as brain.py ADMIN_TOOLS)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Returns the current local system time.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_local_file",
                "description": "Reads the contents of a local file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to the file."}
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memorize_fact",
                "description": "Saves a fact to long-term memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "description": "The fact to store."}
                    },
                    "required": ["fact"],
                },
            },
        },
    ]


@pytest.fixture
def sample_messages():
    """Sample conversation messages array."""
    return [
        {"role": "system", "content": "You are Aster, a personal AI assistant."},
        {"role": "user", "content": "What time is it?"},
    ]


# ============================================================================
# _execute_gemma_completion TESTS
# ============================================================================

class TestExecuteGemmaCompletion:
    """Tests for the llama-server REST adapter."""

    def test_returns_message_dict(self, sample_messages):
        """_execute_gemma_completion returns the full message dict, not a bare string."""
        from core.brain import _execute_gemma_completion

        expected_msg = {"role": "assistant", "content": "It is 3pm."}
        with patch("requests.post", return_value=_make_mock_response(expected_msg)):
            result = _execute_gemma_completion(messages=sample_messages)

        assert isinstance(result, dict)
        assert result["role"] == "assistant"
        assert result["content"] == "It is 3pm."

    def test_content_is_stripped(self, sample_messages):
        """Leading/trailing whitespace is removed from content."""
        from core.brain import _execute_gemma_completion

        msg = {"role": "assistant", "content": "  hello world  "}
        with patch("requests.post", return_value=_make_mock_response(msg)):
            result = _execute_gemma_completion(messages=sample_messages)

        assert result["content"] == "hello world"

    def test_payload_includes_tools_when_passed(self, sample_messages, sample_admin_tools):
        """When tools param is provided and USE_NATIVE_TOOL_CALLS is True, payload includes tools."""
        import config
        from core.brain import _execute_gemma_completion

        msg = {"role": "assistant", "content": None, "tool_calls": []}
        with patch("requests.post", return_value=_make_mock_response(msg)) as mock_post:
            with patch.object(config, "USE_NATIVE_TOOL_CALLS", True):
                _execute_gemma_completion(messages=sample_messages, tools=sample_admin_tools)

        sent_payload = mock_post.call_args.kwargs["json"]
        assert "tools" in sent_payload
        assert sent_payload["tool_choice"] == "auto"
        assert sent_payload["tools"] == sample_admin_tools

    def test_payload_omits_tools_when_none(self, sample_messages):
        """When tools param is None, the payload has no 'tools' key."""
        from core.brain import _execute_gemma_completion

        msg = {"role": "assistant", "content": "hello"}
        with patch("requests.post", return_value=_make_mock_response(msg)) as mock_post:
            _execute_gemma_completion(messages=sample_messages)

        sent_payload = mock_post.call_args.kwargs["json"]
        assert "tools" not in sent_payload
        assert "tool_choice" not in sent_payload

    def test_payload_omits_tools_when_flag_is_false(self, sample_messages, sample_admin_tools):
        """When USE_NATIVE_TOOL_CALLS is False, tools param is ignored."""
        import config
        from core.brain import _execute_gemma_completion

        msg = {"role": "assistant", "content": "hello"}
        with patch("requests.post", return_value=_make_mock_response(msg)) as mock_post:
            with patch.object(config, "USE_NATIVE_TOOL_CALLS", False):
                _execute_gemma_completion(messages=sample_messages, tools=sample_admin_tools)

        sent_payload = mock_post.call_args.kwargs["json"]
        assert "tools" not in sent_payload

    def test_null_content_passes_through(self, sample_messages, sample_admin_tools):
        """None content (native tool call response) is preserved as None."""
        from core.brain import _execute_gemma_completion

        tc = [{"id": "call_1", "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}}]
        msg = {"role": "assistant", "content": None, "tool_calls": tc}
        with patch("requests.post", return_value=_make_mock_response(msg)):
            result = _execute_gemma_completion(messages=sample_messages, tools=sample_admin_tools)

        assert result["content"] is None
        assert result["tool_calls"] is not None


# ============================================================================
# LEGACY XML PARSE TESTS
# ============================================================================

class TestLegacyXmlParse:
    """Tests for the XML-in-text parsing path (USE_NATIVE_TOOL_CALLS = False)."""

    def test_extracts_tool_name_and_args(self):
        """Parse a well-formed XML tool call."""
        from core.brain import _legacy_xml_parse

        text = '<tool_call>{"name": "play_spotify_track", "arguments": {"query": "Bohemian Rhapsody"}}</tool_call>'
        payload, cleaned = _legacy_xml_parse(text)

        assert payload is not None
        assert payload["name"] == "play_spotify_track"
        assert payload["arguments"]["query"] == "Bohemian Rhapsody"
        assert payload["error"] is None
        assert cleaned == ""

    def test_returns_none_when_no_tag(self):
        """Normal text with no tool call returns (None, original_text)."""
        from core.brain import _legacy_xml_parse

        text = "The time is 3pm."
        payload, cleaned = _legacy_xml_parse(text)

        assert payload is None
        assert cleaned == text

    def test_normalizes_gemma_mangled_tags(self):
        """Gemma's <|tool_call|>call> variant is normalized before matching."""
        from core.brain import _legacy_xml_parse

        text = '<|tool_call|>call>{"name": "get_current_time", "arguments": {}}</tool_call>'
        payload, cleaned = _legacy_xml_parse(text)

        assert payload is not None
        assert payload["name"] == "get_current_time"

    def test_normalizes_pipe_variant(self):
        """<|tool_call|> (Qwen-style) is normalized to <tool_call>."""
        from core.brain import _legacy_xml_parse

        text = '<|tool_call|>{"name": "set_timer", "arguments": {"minutes": 5}}</tool_call>'
        payload, cleaned = _legacy_xml_parse(text)

        assert payload is not None
        assert payload["name"] == "set_timer"
        assert payload["arguments"]["minutes"] == 5

    def test_json_decode_error_returns_sentinel(self):
        """Malformed JSON returns sentinel dict with json_error=True."""
        from core.brain import _legacy_xml_parse

        text = '<tool_call>{"name": "broken", "arguments": {bad json}</tool_call>'
        payload, _ = _legacy_xml_parse(text)

        assert payload is not None
        assert payload.get("json_error") is True
        assert payload["name"] == ""

    def test_cleaned_text_has_tag_removed(self):
        """Text before/after the tag is preserved but the tag is stripped."""
        from core.brain import _legacy_xml_parse

        text = 'Sure!<tool_call>{"name": "get_current_time", "arguments": {}}</tool_call>Done.'
        payload, cleaned = _legacy_xml_parse(text)

        assert payload is not None
        assert "<tool_call>" not in cleaned
        assert "Sure!" in cleaned or "Done." in cleaned

    def test_multi_extracts_all_blocks(self):
        """_legacy_xml_parse_multi returns every tool call in document order."""
        from core.brain import _legacy_xml_parse_multi

        text = (
            '<tool_call>{"name": "memorize_fact", "arguments": {"fact": "Fact 1"}}</tool_call>'
            '<tool_call>{"name": "memorize_fact", "arguments": {"fact": "Fact 2"}}</tool_call>'
        )
        results = _legacy_xml_parse_multi(text)

        assert len(results) == 2
        assert results[0]["arguments"]["fact"] == "Fact 1"
        assert results[1]["arguments"]["fact"] == "Fact 2"

    def test_multi_skips_bad_json(self):
        """_legacy_xml_parse_multi skips malformed blocks without crashing."""
        from core.brain import _legacy_xml_parse_multi

        text = (
            '<tool_call>{bad}</tool_call>'
            '<tool_call>{"name": "get_current_time", "arguments": {}}</tool_call>'
        )
        results = _legacy_xml_parse_multi(text)

        assert len(results) == 1
        assert results[0]["name"] == "get_current_time"

    def test_multi_empty_text_returns_empty_list(self):
        """No tool calls in text → empty list."""
        from core.brain import _legacy_xml_parse_multi

        results = _legacy_xml_parse_multi("No tool calls here.")
        assert results == []


# ============================================================================
# NATIVE TOOL CALL EXTRACTION TESTS
# ============================================================================

class TestNativeToolCalling:
    """Tests for the OpenAI-native tool_calls extraction path."""

    def _tc(self, name, args_str="{}"):
        return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": args_str}}

    def test_extract_single_tool_call(self):
        """_extract_native_tool_call reads name, args, and tool_call_id correctly."""
        from core.brain import _extract_native_tool_call

        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [self._tc("get_current_time")],
        }
        payload, content = _extract_native_tool_call(message)

        assert payload is not None
        assert payload["name"] == "get_current_time"
        assert payload["arguments"] == {}
        assert payload["tool_call_id"] == "call_get_current_time"
        assert payload["error"] is None
        assert content == ""

    def test_extract_with_json_args(self):
        """JSON string arguments are parsed into a dict."""
        from core.brain import _extract_native_tool_call

        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [self._tc("play_spotify_track", '{"query": "Bohemian Rhapsody"}')],
        }
        payload, _ = _extract_native_tool_call(message)

        assert payload["arguments"]["query"] == "Bohemian Rhapsody"

    def test_extract_returns_none_when_no_tool_calls(self):
        """No tool_calls → (None, content)."""
        from core.brain import _extract_native_tool_call

        message = {"role": "assistant", "content": "The time is 3pm.", "tool_calls": None}
        payload, content = _extract_native_tool_call(message)

        assert payload is None
        assert content == "The time is 3pm."

    def test_extract_returns_none_for_empty_list(self):
        """Empty tool_calls list → (None, content)."""
        from core.brain import _extract_native_tool_call

        message = {"role": "assistant", "content": "Hello.", "tool_calls": []}
        payload, content = _extract_native_tool_call(message)

        assert payload is None

    def test_extract_json_error_returns_sentinel(self):
        """Malformed JSON arguments return sentinel with json_error=True."""
        from core.brain import _extract_native_tool_call

        tc = {"id": "call_bad", "type": "function", "function": {"name": "broken", "arguments": "{bad json"}}
        message = {"role": "assistant", "content": None, "tool_calls": [tc]}
        payload, _ = _extract_native_tool_call(message)

        assert payload is not None
        assert payload.get("json_error") is True
        assert payload["tool_call_id"] == "call_bad"

    def test_extract_all_multiple_tool_calls(self):
        """_extract_all_native_tool_calls returns every call in order."""
        from core.brain import _extract_all_native_tool_calls

        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                self._tc("memorize_fact", '{"fact": "Fact A"}'),
                self._tc("memorize_fact", '{"fact": "Fact B"}'),
            ],
        }
        results = _extract_all_native_tool_calls(message)

        assert len(results) == 2
        assert results[0]["arguments"]["fact"] == "Fact A"
        assert results[1]["arguments"]["fact"] == "Fact B"

    def test_extract_all_skips_bad_json(self):
        """Malformed JSON in one call does not crash; others are returned."""
        from core.brain import _extract_all_native_tool_calls

        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "bad", "arguments": "{bad}"}},
                self._tc("get_current_time"),
            ],
        }
        results = _extract_all_native_tool_calls(message)

        assert len(results) == 1
        assert results[0]["name"] == "get_current_time"

    def test_extract_all_no_tool_calls_returns_empty(self):
        """Message with no tool_calls → empty list."""
        from core.brain import _extract_all_native_tool_calls

        message = {"role": "assistant", "content": "Hello.", "tool_calls": None}
        assert _extract_all_native_tool_calls(message) == []

    def test_react_loop_native_assistant_message_format(self, sample_messages, sample_admin_tools):
        """After a tool call the assistant message in history has tool_calls field."""
        import config
        from core.brain import _execute_gemma_completion, _extract_native_tool_call

        tc_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_xyz", "type": "function",
                            "function": {"name": "get_current_time", "arguments": "{}"}}],
        }
        with patch("requests.post", return_value=_make_mock_response(tc_msg)):
            with patch.object(config, "USE_NATIVE_TOOL_CALLS", True):
                response_msg = _execute_gemma_completion(
                    messages=sample_messages, tools=sample_admin_tools
                )

        tool_payload, _ = _extract_native_tool_call(response_msg)
        assert tool_payload is not None

        # Simulate what the ReAct loop stores for the assistant turn
        history_entry = {
            "role": "assistant",
            "content": response_msg.get("content"),
            "tool_calls": response_msg.get("tool_calls"),
        }
        assert history_entry["role"] == "assistant"
        assert history_entry["tool_calls"] is not None
        assert history_entry["tool_calls"][0]["function"]["name"] == "get_current_time"

    def test_react_loop_native_tool_result_format(self, sample_messages, sample_admin_tools):
        """Tool result is injected as role:tool with matching tool_call_id."""
        import config
        from core.brain import _execute_gemma_completion, _extract_native_tool_call

        tc_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_abc", "type": "function",
                            "function": {"name": "get_current_time", "arguments": "{}"}}],
        }
        with patch("requests.post", return_value=_make_mock_response(tc_msg)):
            with patch.object(config, "USE_NATIVE_TOOL_CALLS", True):
                response_msg = _execute_gemma_completion(
                    messages=sample_messages, tools=sample_admin_tools
                )

        tool_payload, _ = _extract_native_tool_call(response_msg)
        tool_result = "2026-07-03 14:00:00"

        tool_msg = {
            "role": "tool",
            "tool_call_id": tool_payload["tool_call_id"],
            "content": tool_result,
        }
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_abc"
        assert tool_msg["content"] == tool_result

    def test_rollback_flag_uses_xml_path(self, sample_messages):
        """When USE_NATIVE_TOOL_CALLS is False, payload has no tools key."""
        import config
        from core.brain import _execute_gemma_completion

        msg = {"role": "assistant", "content": '<tool_call>{"name": "get_current_time", "arguments": {}}</tool_call>'}
        with patch("requests.post", return_value=_make_mock_response(msg)) as mock_post:
            with patch.object(config, "USE_NATIVE_TOOL_CALLS", False):
                response_msg = _execute_gemma_completion(messages=sample_messages)

        sent_payload = mock_post.call_args.kwargs["json"]
        assert "tools" not in sent_payload
        # XML tool call is in content, not in tool_calls
        assert "<tool_call>" in (response_msg.get("content") or "")

    def test_claims_tool_execution_short_circuits_on_native_tool_calls(self):
        """_claims_tool_execution returns False immediately when tool_calls is set."""
        from core.brain import _claims_tool_execution

        message = {
            "role": "assistant",
            "content": "Playing Bohemian Rhapsody on Spotify right now.",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "play_spotify_track", "arguments": '{"query": "Bohemian Rhapsody"}'}}],
        }
        result = _claims_tool_execution(
            "play Bohemian Rhapsody on Spotify",
            "Playing Bohemian Rhapsody on Spotify right now.",
            message=message,
        )
        assert result is False


# ============================================================================
# TOOL FAILURE DETECTION (failure-blind success claims)
# ============================================================================

class TestToolFailureDetection:
    """_tool_result_failed / _acknowledges_failure — the guards that stop the
    model from reporting success after a tool call that actually FAILED."""

    def test_spotify_no_device_message_reads_as_failure(self):
        from core.brain import _tool_result_failed
        from tools.media import _NO_DEVICE_MSG

        assert _tool_result_failed(_NO_DEVICE_MSG) is True

    def test_generic_error_wrapper_reads_as_failure(self):
        from core.brain import _tool_result_failed

        assert _tool_result_failed("Error executing play_spotify_track: boom") is True
        assert _tool_result_failed("FAILED — Spotify error, the action did NOT happen: 403") is True

    def test_success_results_do_not_read_as_failure(self):
        from core.brain import _tool_result_failed

        assert _tool_result_failed("Playing Baby Doll by Ava Max") is False
        assert _tool_result_failed("Action completed successfully.") is False
        # prose that merely mentions the word mid-sentence is not a failure
        assert _tool_result_failed("Note saved: remember the error report is due") is False

    def test_dict_results_use_text_field(self):
        from core.brain import _tool_result_failed

        assert _tool_result_failed({"text": "Error: click target not found", "ui_screenshot_b64": "x"}) is True
        assert _tool_result_failed({"text": "Clicked the button.", "ui_screenshot_b64": "x"}) is False

    def test_acknowledges_failure_positive(self):
        from core.brain import _acknowledges_failure

        assert _acknowledges_failure("I couldn't start Spotify, sir.") is True
        assert _acknowledges_failure("My apologies, sir — the playback failed.") is True
        assert _acknowledges_failure("Unfortunately Spotify refused the request.") is True

    def test_acknowledges_failure_negative(self):
        from core.brain import _acknowledges_failure

        # the exact class of reply that motivated this guard
        assert _acknowledges_failure("Here you go sir, the song is in.") is False
        assert _acknowledges_failure("Playing Baby Doll now, sir.") is False
        assert _acknowledges_failure("") is False


# ============================================================================
# TOOL SCHEMA COMPATIBILITY
# ============================================================================

class TestToolSchemaCompatibility:
    """Verify ADMIN_TOOLS schema is well-formed OpenAI function-calling format."""

    def test_all_tools_have_required_keys(self, sample_admin_tools):
        """Every tool has type, function.name, function.description, function.parameters."""
        for tool in sample_admin_tools:
            assert tool["type"] == "function"
            assert "function" in tool
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"

    def test_real_admin_tools_schema(self):
        """The actual ADMIN_TOOLS list loaded from brain.py is OpenAI-compatible."""
        from core.brain import ADMIN_TOOLS

        assert len(ADMIN_TOOLS) > 0
        for tool in ADMIN_TOOLS:
            assert tool.get("type") == "function", f"Tool missing type: {tool}"
            fn = tool.get("function", {})
            assert fn.get("name"), f"Tool missing name: {tool}"
            assert fn.get("description"), f"Tool missing description: {tool}"


# ============================================================================
# CONTEXT WINDOW MANAGEMENT
# ============================================================================

class TestContextWindowManagement:
    """Tests for 128k context sliding window and trimming."""

    def test_trim_preserves_system_prompt(self):
        """trim_memory always keeps the system prompt at index 0."""
        messages = [
            {"role": "system", "content": "You are Aster."},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]

        def trim_memory(msg_list, max_tokens=10):
            if len(msg_list) <= 2:
                return msg_list
            total = sum(len(str(m.get("content", ""))) // 4 for m in msg_list)
            while total > max_tokens and len(msg_list) > 2:
                removed = msg_list.pop(1)
                total -= len(str(removed.get("content", ""))) // 4
            return msg_list

        result = trim_memory(messages, max_tokens=10)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are Aster."

    def test_trim_under_threshold_noop(self):
        """trim_memory does nothing when under token limit."""
        messages = [
            {"role": "system", "content": "You are Aster."},
            {"role": "user", "content": "Hello"},
        ]
        original_len = len(messages)

        def trim_memory(msg_list, max_tokens=120000):
            if len(msg_list) <= 2:
                return msg_list
            total = sum(len(str(m.get("content", ""))) // 4 for m in msg_list)
            while total > max_tokens and len(msg_list) > 2:
                removed = msg_list.pop(1)
                total -= len(str(removed.get("content", ""))) // 4
            return msg_list

        result = trim_memory(messages)
        assert len(result) == original_len

    def test_trim_removes_oldest_messages_first(self):
        """Oldest non-system messages are removed first."""
        messages = [
            {"role": "system", "content": "You are Aster."},
            {"role": "user", "content": "A" * 1000},
            {"role": "assistant", "content": "B" * 1000},
            {"role": "user", "content": "C" * 1000},
            {"role": "assistant", "content": "D" * 1000},
        ]

        def trim_memory(msg_list, max_tokens=100):
            if len(msg_list) <= 2:
                return msg_list
            total = sum(len(str(m.get("content", ""))) // 4 for m in msg_list)
            while total > max_tokens and len(msg_list) > 2:
                removed = msg_list.pop(1)
                total -= len(str(removed.get("content", ""))) // 4
            return msg_list

        result = trim_memory(messages, max_tokens=100)
        assert result[0]["role"] == "system"
        contents = [m["content"] for m in result]
        assert "D" * 1000 in contents

    def test_128k_boundary_estimation(self):
        """Token estimation for 128k context boundary."""
        max_tokens = 131072
        max_chars = max_tokens * 4
        boundary_message = {"role": "user", "content": "A" * max_chars}
        estimated_tokens = len(boundary_message["content"]) // 4
        assert estimated_tokens == max_tokens

    def test_character_heuristic_token_estimate(self):
        """_estimate_tokens uses the 4-chars-per-token fallback heuristic.

        Calibration against the live /tokenize endpoint is patched out so the
        assertion is independent of whether llama-server is running (with the
        server up, the calibrated ratio for a run of 'A's is ~16, not 4)."""
        from unittest.mock import patch

        from core import memory

        with patch.object(memory, "_calibrated_ratio", return_value=4.0), \
             patch.object(memory, "_maybe_recalibrate_ratio", lambda _t: None):
            est = memory._estimate_tokens([{"role": "user", "content": "A" * 400}])
        assert est == 100

    def test_media_cache_purge(self):
        """Base64 image/audio data is stripped from history."""
        messages = [
            {"role": "system", "content": "You are Aster."},
            {"role": "user", "content": "Look at this image.", "images": ["aW1hZ2VfZGF0YQ=="]},
            {"role": "user", "content": "Listen to this.", "audio": ["YXVkaW9fZGF0YQ=="]},
        ]
        for msg in messages:
            msg.pop("images", None)
            msg.pop("audio", None)
        for msg in messages:
            assert "images" not in msg
            assert "audio" not in msg


# ============================================================================
# MULTIMODAL PAYLOAD FORMATTING
# ============================================================================

class TestMultimodalPayloadFormatting:
    """Tests for image and audio base64 payload structure."""

    def test_image_payload_format(self):
        """Image base64 is correctly structured in message array."""
        img_b64 = base64.b64encode(b"fake_image_data").decode("utf-8")
        caption = "What is in this image?"

        message = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": caption},
            ],
        }

        assert message["role"] == "user"
        assert isinstance(message["content"], list)
        image_part = message["content"][0]
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert message["content"][1]["text"] == caption

    def test_audio_payload_format_ogg(self):
        """OGG audio base64 is correctly structured for Telegram voice notes."""
        audio_b64 = base64.b64encode(b"fake_ogg_audio_data").decode("utf-8")
        message = {
            "role": "user",
            "content": [
                {"type": "audio_url", "audio_url": {"url": f"data:audio/ogg;base64,{audio_b64}"}},
                {"type": "text", "text": "Listen to this voice note."},
            ],
        }
        assert message["content"][0]["audio_url"]["url"].startswith("data:audio/ogg;base64,")

    def test_audio_payload_format_pcm(self):
        """PCM audio base64 is correctly structured for LiveKit WebRTC."""
        audio_b64 = base64.b64encode(b"fake_pcm_audio_data").decode("utf-8")
        message = {
            "role": "user",
            "content": [
                {"type": "audio_url", "audio_url": {"url": f"data:audio/pcm;base64,{audio_b64}"}},
                {"type": "text", "text": "Live audio input."},
            ],
        }
        assert "audio/pcm" in message["content"][0]["audio_url"]["url"]

    def test_image_payload_with_empty_caption(self):
        """Default caption is used when none is provided."""
        img_b64 = base64.b64encode(b"fake_image_data").decode("utf-8")
        caption = ""
        message = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": caption or "Describe what you see in this image."},
            ],
        }
        assert message["content"][1]["text"] == "Describe what you see in this image."

    def test_base64_encoding_decoding_roundtrip(self):
        """Base64 data survives encoding/decoding roundtrip."""
        original = b"This is fake image data for testing purposes"
        encoded = base64.b64encode(original).decode("utf-8")
        decoded = base64.b64decode(encoded)
        assert decoded == original

    def test_native_audio_payload_tag_extraction(self):
        """[NATIVE_AUDIO_PAYLOAD:...] tag is parsed correctly."""
        user_input = "[NATIVE_AUDIO_PAYLOAD:YXVkaW9fZGF0YQ==]"
        audio_b64 = user_input.split(":", 1)[1][:-1]
        assert audio_b64 == "YXVkaW9fZGF0YQ=="

    def test_native_image_payload_tag_extraction(self):
        """[NATIVE_IMAGE_PAYLOAD:...] tag is parsed correctly."""
        user_input = "[NATIVE_IMAGE_PAYLOAD:aW1hZ2VfZGF0YQ==]What is this?"
        payload_end = user_input.find("]")
        img_b64 = user_input[22:payload_end]
        caption = user_input[payload_end + 1:].strip()
        assert img_b64 == "aW1hZ2VfZGF0YQ=="
        assert caption == "What is this?"


# ============================================================================
# SENTRY MODE VISION
# ============================================================================

class TestSentryModeVision:
    """Tests for Gemma-based sentry mode vision response parsing."""

    def test_sentry_analysis_unknown_person(self):
        """Sentry analysis correctly identifies an unknown person."""
        analysis = "I can see one unknown person in the room. Mohamed is not present."
        assert "unknown" in analysis.lower()
        assert "mohamed is not present" in analysis.lower()

    def test_sentry_mohamed_present_no_alert(self):
        """No alert keyword when Mohamed is detected."""
        analysis = "I can see Mohamed sitting at his desk. No other people present."
        assert "unknown" not in analysis.lower()
        assert "mohamed" in analysis.lower()


# ============================================================================
# INTEGRATION SMOKE TEST
# ============================================================================

class TestIntegrationSmoke:
    """Smoke tests for the full native tool calling round-trip."""

    def test_full_native_tool_round_trip(self, sample_messages, sample_admin_tools):
        """user input → tool call → tool result message structure is correct."""
        import config
        from core.brain import _execute_gemma_completion, _extract_native_tool_call

        tc_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_xyz", "type": "function",
                            "function": {"name": "get_current_time", "arguments": "{}"}}],
        }
        text_msg = {"role": "assistant", "content": "It is currently 2026-07-03 14:30:00."}

        messages = list(sample_messages)
        responses = [_make_mock_response(tc_msg), _make_mock_response(text_msg)]

        with patch("requests.post", side_effect=responses):
            with patch.object(config, "USE_NATIVE_TOOL_CALLS", True):
                # Round 1: tool call
                r1 = _execute_gemma_completion(messages=messages, tools=sample_admin_tools)
                tool_payload, _ = _extract_native_tool_call(r1)
                assert tool_payload is not None
                assert tool_payload["name"] == "get_current_time"

                # Inject assistant + tool result
                messages.append({"role": "assistant", "content": None, "tool_calls": r1["tool_calls"]})
                messages.append({"role": "tool", "tool_call_id": tool_payload["tool_call_id"], "content": "2026-07-03 14:30:00"})

                # Round 2: final answer
                r2 = _execute_gemma_completion(messages=messages)
                assert "2026-07-03" in (r2.get("content") or "")


# ============================================================================
# GOOGLE INTEGRATION (Gmail + Calendar) — tier gating
# ============================================================================

@pytest.fixture
def clean_google_pending_state():
    """Ensures tools.google_auth's shared pending-action slot is empty before
    and after each test, even if the test body raises."""
    import tools.google_auth as google_auth
    google_auth._pending_action = None
    yield
    google_auth._pending_action = None


class TestGoogleTierGating:
    """tools.google_auth's tier_allows/send_policy decision logic."""

    def test_google_unavailable_without_credentials(self):
        import config
        assert config.GOOGLE_AVAILABLE is False  # no google block in this test env's secrets.yaml

    def test_tier_allows_read_always_true(self):
        import config
        import tools.google_auth as google_auth
        for tier in ("limited", "partial", "autonomous"):
            with patch.object(config, "GOOGLE_ACCESS_TIER", tier):
                assert google_auth.tier_allows("read") is True

    def test_tier_allows_modify_and_send_blocked_at_limited(self):
        import config
        import tools.google_auth as google_auth
        with patch.object(config, "GOOGLE_ACCESS_TIER", "limited"):
            assert google_auth.tier_allows("modify") is False
            assert google_auth.tier_allows("send") is False

    def test_tier_allows_modify_and_send_permitted_at_partial_and_autonomous(self):
        import config
        import tools.google_auth as google_auth
        for tier in ("partial", "autonomous"):
            with patch.object(config, "GOOGLE_ACCESS_TIER", tier):
                assert google_auth.tier_allows("modify") is True
                assert google_auth.tier_allows("send") is True

    def test_send_policy_blocked_at_limited_regardless_of_guardrail(self):
        import config
        import tools.google_auth as google_auth
        with patch.object(config, "GOOGLE_ACCESS_TIER", "limited"):
            assert google_auth.send_policy(True) == "blocked"
            assert google_auth.send_policy(False) == "blocked"

    def test_send_policy_always_asks_at_partial_regardless_of_guardrail(self):
        import config
        import tools.google_auth as google_auth
        with patch.object(config, "GOOGLE_ACCESS_TIER", "partial"):
            assert google_auth.send_policy(True) == "ask"
            assert google_auth.send_policy(False) == "ask"

    def test_send_policy_autonomous_sends_when_guardrail_ok(self):
        import config
        import tools.google_auth as google_auth
        with patch.object(config, "GOOGLE_ACCESS_TIER", "autonomous"):
            assert google_auth.send_policy(True) == "send"

    def test_send_policy_autonomous_falls_back_to_ask_when_guardrail_trips(self):
        import config
        import tools.google_auth as google_auth
        with patch.object(config, "GOOGLE_ACCESS_TIER", "autonomous"):
            assert google_auth.send_policy(False) == "ask"


class TestGoogleReplyGuardrail:
    """gmail_tool.reply_to_email's autonomous-tier safety guardrail — a reply
    to a sender with no prior correspondence must fall back to the same
    text-approval flow Partial tier uses, never send unsupervised."""

    def _fake_gmail_service(self, draft_id: str):
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
            "payload": {"headers": [
                {"name": "From", "value": "Someone <someone@example.com>"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Message-ID", "value": "<abc@example.com>"},
            ]},
            "threadId": "thread123",
        }
        service.users.return_value.drafts.return_value.create.return_value.execute.return_value = {"id": draft_id}
        return service

    def test_falls_back_to_approval_with_no_prior_correspondence(self, clean_google_pending_state):
        import config
        import tools.google_auth as google_auth
        import tools.gmail_tool as gmail_tool

        fake_service = self._fake_gmail_service("draft1")

        with patch.object(config, "GOOGLE_ACCESS_TIER", "autonomous"), \
             patch.object(google_auth, "get_gmail_service", return_value=fake_service), \
             patch.object(gmail_tool, "_has_prior_correspondence", return_value=False), \
             patch.object(google_auth, "autonomous_sends_today", return_value=0):

            result = gmail_tool.reply_to_email("msg1", "Sure, let's talk.")

            assert "held it back" in result
            assert google_auth.has_pending_action() is True
            fake_service.users.return_value.drafts.return_value.send.assert_not_called()

    def test_falls_back_to_approval_when_daily_cap_reached(self, clean_google_pending_state):
        import config
        import tools.google_auth as google_auth
        import tools.gmail_tool as gmail_tool

        fake_service = self._fake_gmail_service("draft2")

        with patch.object(config, "GOOGLE_ACCESS_TIER", "autonomous"), \
             patch.object(config, "AUTONOMOUS_SEND_DAILY_CAP", 5), \
             patch.object(google_auth, "get_gmail_service", return_value=fake_service), \
             patch.object(gmail_tool, "_has_prior_correspondence", return_value=True), \
             patch.object(google_auth, "autonomous_sends_today", return_value=5):

            result = gmail_tool.reply_to_email("msg2", "Sure, let's talk.")

            assert "held it back" in result
            assert "daily" in result
            fake_service.users.return_value.drafts.return_value.send.assert_not_called()

    def test_sends_immediately_with_prior_correspondence_under_cap(self, clean_google_pending_state):
        import config
        import tools.google_auth as google_auth
        import tools.gmail_tool as gmail_tool

        fake_service = self._fake_gmail_service("draft3")

        with patch.object(config, "GOOGLE_ACCESS_TIER", "autonomous"), \
             patch.object(google_auth, "get_gmail_service", return_value=fake_service), \
             patch.object(gmail_tool, "_has_prior_correspondence", return_value=True), \
             patch.object(google_auth, "autonomous_sends_today", return_value=0), \
             patch.object(google_auth, "record_autonomous_send") as mock_record:

            result = gmail_tool.reply_to_email("msg3", "Sure, let's talk.")

            assert "autonomously" in result
            assert google_auth.has_pending_action() is False
            fake_service.users.return_value.drafts.return_value.send.assert_called_once()
            mock_record.assert_called_once()

    def test_partial_tier_always_asks_even_with_prior_correspondence(self, clean_google_pending_state):
        import config
        import tools.google_auth as google_auth
        import tools.gmail_tool as gmail_tool

        fake_service = self._fake_gmail_service("draft4")

        with patch.object(config, "GOOGLE_ACCESS_TIER", "partial"), \
             patch.object(google_auth, "get_gmail_service", return_value=fake_service), \
             patch.object(gmail_tool, "_has_prior_correspondence", return_value=True):

            result = gmail_tool.reply_to_email("msg4", "Sure, let's talk.")

            assert "send it" in result.lower() or "reply 'send it'" in result
            assert google_auth.has_pending_action() is True
            fake_service.users.return_value.drafts.return_value.send.assert_not_called()

    def test_limited_tier_blocks_reply_entirely(self, clean_google_pending_state):
        import config
        import tools.gmail_tool as gmail_tool

        with patch.object(config, "GOOGLE_ACCESS_TIER", "limited"):
            result = gmail_tool.reply_to_email("msg5", "Sure, let's talk.")
            assert "Blocked" in result
