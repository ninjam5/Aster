"""Brain-level tests for the Discord relay gates (QA round 6 gaps).

Rounds 3-5 added: the brain forces `confirm=False` + `assume_guess=True` on a non-exact
relay turn, and blocks the input tools on a relay turn. Only the TOOL-level behaviour
was tested, so a regression in the brain wiring would have gone unnoticed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain


def _tool_call(name, args_json):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "function": {"name": name,
                                                    "arguments": args_json}}]}


@pytest.fixture
def relay_env(monkeypatch):
    """A minimal admin turn: no compaction, no mood, no ambient block, no real sends."""
    monkeypatch.setattr(config, "AUTO_COMPACT_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(brain, "_log_turn_mood", lambda t: None, raising=False)
    monkeypatch.setattr(brain.awareness, "render_context_block", lambda: None, raising=False)
    monkeypatch.setattr(brain, "trim_memory", lambda m: m, raising=False)
    monkeypatch.setattr(brain, "publish_terminal", lambda *a, **k: None, raising=False)
    saved = list(brain.messages)
    yield
    brain.messages[:] = saved


class TestRelayConfirmForcing:
    @pytest.fixture(autouse=True)
    def _pin_contacts(self, monkeypatch):
        # QA round 7: these tests read the owner's real discord_contacts.json; pin it so
        # the outcome cannot depend on the live address book.
        import tools.discord_api as _d
        monkeypatch.setattr(_d, "CONTACTS", {"george": "1", "farah": "2"}, raising=False)

    def test_a_non_exact_relay_forces_confirm_off(self, monkeypatch, relay_env):
        """The model self-asserting confirm=true must NOT approve a guessed target."""
        sent = {}

        def fake_send(target, msg, confirm=False, assume_guess=False):
            sent.update(target=target, confirm=confirm, assume_guess=assume_guess)
            return "FAILED — [CONFIRM REQUIRED: nothing was sent]"

        monkeypatch.setattr(brain, "send_discord_message", fake_send)
        monkeypatch.setattr(
            brain, "_execute_llm_completion",
            lambda **k: _tool_call("send_discord_message",
                                   '{"target_name": "george", "message": "Hi", "confirm": true}'),
            raising=False)

        brain.messages[:] = [brain.messages[0]]
        brain.process_user_input("tell geroge I'll be late", None)

        assert sent, "send_discord_message was never called"
        assert sent["confirm"] is False, "the model must not self-confirm a guessed target"
        assert sent["assume_guess"] is True

    def test_an_exact_relay_is_not_forced(self, monkeypatch, relay_env):
        sent = {}

        def fake_send(target, msg, confirm=False, assume_guess=False):
            sent.update(target=target, confirm=confirm, assume_guess=assume_guess)
            return "[System Note: Message delivered to george on Discord.]"

        monkeypatch.setattr(brain, "send_discord_message", fake_send)
        monkeypatch.setattr(
            brain, "_execute_llm_completion",
            lambda **k: _tool_call("send_discord_message",
                                   '{"target_name": "george", "message": "Hi"}'),
            raising=False)

        brain.messages[:] = [brain.messages[0]]
        brain.process_user_input("tell george I'll be late", None)

        # QA round 7: prove the parser actually classified it as an EXACT relay, or this
        # passed merely because the intent was None (nothing forced).
        assert sent.get("assume_guess") is False
        assert sent.get("target") == "george"
        # QA round 8: the target above is the MODEL's own argument, so it proves nothing
        # about the parser. Assert the parse itself, or deleting the parser stays green.
        intent = brain._extract_discord_message_intent("tell george I'll be late")
        assert intent is not None and intent["exact"] is True and intent["target"] == "george"


class TestRelayToolBlocklist:
    def test_input_tools_are_refused_on_a_relay_turn(self, monkeypatch, relay_env):
        clicked = []
        monkeypatch.setattr(brain.pyautogui, "click", lambda *a: clicked.append(1),
                            raising=False)
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Send", "score": 0.9}, raising=False)
        monkeypatch.setattr(
            brain, "_execute_llm_completion",
            lambda **k: _tool_call("smart_click", '{"goal": "Send"}'), raising=False)

        brain.messages[:] = [brain.messages[0]]
        brain.process_user_input("tell george I'll be late", None)

        assert clicked == [], "a relay turn must not drive the desktop"


@pytest.fixture(autouse=True)
def _isolate_brain_turn(tmp_path, monkeypatch):
    """QA round 8: these tests run a real admin turn, which appended to the real
    Aster_Vault/Conversations/ and could tag the text with a mood (changing the input)."""
    import config as _c
    monkeypatch.setattr(_c, "CONVERSATIONS_DIR", str(tmp_path / "Conversations"), raising=False)
    monkeypatch.setattr(_c, "MOOD_LOG_PATH", str(tmp_path / "emotion_log.jsonl"), raising=False)
    monkeypatch.setattr(brain, "_maybe_tag_text_mood", lambda t: t, raising=False)
