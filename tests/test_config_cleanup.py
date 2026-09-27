"""
Tests for the open-source config-cleanup pass: secrets.yaml/self_config.yaml
externalization, graceful degradation on blank credentials, and removal of
hardcoded owner-identity/contact data from core/brain.py and friends.

No llama-server needed. Uses monkeypatch/tmp_path rather than touching the
real secrets.yaml / self_config.yaml (which hold this installation's real
credentials).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


class TestSecretsLoader:
    """config._load_secrets() / config._secret() mirror the self_config pattern."""

    def test_load_secrets_returns_empty_dict_when_file_missing(self, tmp_path, monkeypatch):
        missing_path = tmp_path / "does_not_exist.yaml"
        monkeypatch.setattr(config, "SECRETS_PATH", str(missing_path))
        assert config._load_secrets() == {}

    def test_load_secrets_returns_empty_dict_on_malformed_yaml(self, tmp_path, monkeypatch):
        bad_file = tmp_path / "bad_secrets.yaml"
        bad_file.write_text("not: valid: yaml: [", encoding="utf-8")
        monkeypatch.setattr(config, "SECRETS_PATH", str(bad_file))
        assert config._load_secrets() == {}

    def test_load_secrets_returns_empty_dict_when_root_is_not_a_mapping(self, tmp_path, monkeypatch):
        list_file = tmp_path / "list_secrets.yaml"
        list_file.write_text("- one\n- two\n", encoding="utf-8")
        monkeypatch.setattr(config, "SECRETS_PATH", str(list_file))
        assert config._load_secrets() == {}

    def test_secret_walks_dotted_path(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", {"spotify": {"client_id": "abc123"}})
        assert config._secret("spotify", "client_id") == "abc123"

    def test_secret_returns_default_for_missing_key(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", {"spotify": {}})
        assert config._secret("spotify", "client_id", default="") == ""
        assert config._secret("nonexistent", "key", default="fallback") == "fallback"

    def test_secret_never_raises_on_non_dict_secrets(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", None)
        assert config._secret("spotify", "client_id", default="") == ""


class TestCredentialGatingLogic:
    """Blank credentials must disable an integration, not half-configure it.

    These reproduce the exact boolean expressions config.py uses for its
    AVAILABLE flags — the live flags are fixed at import time from the real
    secrets.yaml, so these test the gating logic itself via config._secret()
    rather than re-triggering module import.
    """

    def test_spotify_gate_false_when_both_blank(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", {"spotify": {"client_id": "", "client_secret": ""}})
        client_id = config._secret("spotify", "client_id", default="")
        client_secret = config._secret("spotify", "client_secret", default="")
        assert bool(client_id and client_secret) is False

    def test_spotify_gate_false_when_only_one_set(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", {"spotify": {"client_id": "abc", "client_secret": ""}})
        client_id = config._secret("spotify", "client_id", default="")
        client_secret = config._secret("spotify", "client_secret", default="")
        assert bool(client_id and client_secret) is False

    def test_spotify_gate_true_when_both_set(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", {"spotify": {"client_id": "abc", "client_secret": "def"}})
        client_id = config._secret("spotify", "client_id", default="")
        client_secret = config._secret("spotify", "client_secret", default="")
        assert bool(client_id and client_secret) is True

    def test_real_installation_spotify_available_matches_credentials(self):
        """Sanity check the live flag agrees with the live credentials (this
        installation has real Spotify creds configured)."""
        assert config.SPOTIFY_AVAILABLE == bool(config.SPOTIPY_CLIENT_ID and config.SPOTIPY_CLIENT_SECRET)

    def test_telegram_bot_none_when_token_blank(self, monkeypatch):
        monkeypatch.setattr(config, "SECRETS", {"telegram": {"bot_token": ""}})
        token = config._secret("telegram", "bot_token", default="")
        assert bool(token) is False


class TestNoHardcodedCredentialsInSource:
    """The real secret VALUES (whatever is currently in the gitignored
    secrets.yaml) must never appear as literals in tracked .py files.

    Reads the live values from `config`/`tools.discord_api`/`webrtc_bridge`
    rather than hardcoding any fragment here — this test file must not itself
    contain real secret material.
    """

    SCANNED_FILES = [
        "config.py",
        "webrtc_bridge.py",
        os.path.join("tools", "discord_api.py"),
        os.path.join("tools", "face_server.py"),
        os.path.join("core", "brain.py"),
    ]

    def test_no_real_credential_literals_in_source(self):
        import tools.discord_api as discord_api

        live_secrets = [
            config.SPOTIPY_CLIENT_ID,
            config.SPOTIPY_CLIENT_SECRET,
            config.TELEGRAM_BOT_TOKEN,
            discord_api.DISCORD_BOT_TOKEN,
        ]
        live_secrets = [s for s in live_secrets if s and len(s) >= 8]  # skip blanks/too-short-to-be-meaningful

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel_path in self.SCANNED_FILES:
            full_path = os.path.join(root, rel_path)
            with open(full_path, "r", encoding="utf-8") as f:
                text = f.read()
            for secret in live_secrets:
                assert secret not in text, f"Found a live credential value hardcoded in {rel_path}"


class TestDiscordFemaleNamesFromConfig:
    """DISCORD_FEMALE_NAMES must be config-driven, not a brain.py literal."""

    def test_brain_has_no_module_level_constant(self):
        import core.brain as brain

        assert not hasattr(brain, "DISCORD_FEMALE_NAMES"), (
            "brain.py should read config.DISCORD_FEMALE_NAMES, not define its own constant"
        )

    def test_honorific_reflects_config_value(self, monkeypatch):
        import core.brain as brain

        monkeypatch.setattr(config, "DISCORD_FEMALE_NAMES", {"alice"})
        assert brain._discord_honorific("alice") == "Ma'am"
        assert brain._discord_honorific("bob") == "Sir"

    def test_honorific_empty_set_defaults_everyone_to_sir(self, monkeypatch):
        import core.brain as brain

        monkeypatch.setattr(config, "DISCORD_FEMALE_NAMES", set())
        assert brain._discord_honorific("farah") == "Sir"

    def test_enforce_honorific_uses_config_names_not_hardcoded(self, monkeypatch):
        import core.brain as brain

        monkeypatch.setattr(config, "DISCORD_FEMALE_NAMES", {"zara"})
        out = brain._enforce_discord_honorific("zara", "Yes Sir, tell Mr. Zara I said hi.")
        assert out == "Yes Ma'am, tell Ms. Zara I said hi."
        # A name NOT in the (monkeypatched) female set must be left alone even
        # though it used to be hardcoded — proves the regex is config-driven.
        out2 = brain._enforce_discord_honorific("zara", "Mr. Farah will be informed too.")
        assert "Mr. Farah" in out2


class TestOwnerNameGeneralization:
    """ADMIN_TOOLS descriptions say 'the user'; Discord-facing text is dynamic."""

    def test_admin_tools_no_literal_mohamed(self):
        from core.brain import ADMIN_TOOLS

        offenders = [
            t["function"]["name"] for t in ADMIN_TOOLS
            if "Mohamed" in t["function"].get("description", "")
        ]
        assert offenders == [], f"ADMIN_TOOLS descriptions still hardcode 'Mohamed': {offenders}"

    def test_admin_tools_no_real_friend_names_as_examples(self):
        """Real contact first names shouldn't show up as illustrative example
        values anywhere in a tool's schema — including nested parameter
        descriptions, e.g. send_discord_message's target_name example."""
        import json

        from core.brain import ADMIN_TOOLS

        real_names = ("farah", "emily", "masky", "tiger", "tolba", "barakat", "george", "ninja", "adham")
        offenders = []
        for t in ADMIN_TOOLS:
            full_schema_text = json.dumps(t).lower()
            if any(name in full_schema_text for name in real_names):
                offenders.append(t["function"]["name"])
        assert offenders == [], f"ADMIN_TOOLS schemas reference real contact names: {offenders}"

    def test_discord_tools_forward_renamed(self):
        from core.brain import DISCORD_TOOLS

        names = [t["function"]["name"] for t in DISCORD_TOOLS]
        assert "forward_to_owner" in names
        assert "forward_to_mohamed" not in names

    def test_discord_tools_description_uses_configured_owner_name(self):
        """DISCORD_TOOLS descriptions are built once at import time from
        config.OWNER_NAME — verify they actually contain that value rather
        than a different hardcoded name."""
        from core.brain import DISCORD_TOOLS

        forward_tool = next(t for t in DISCORD_TOOLS if t["function"]["name"] == "forward_to_owner")
        assert config.OWNER_NAME in forward_tool["function"]["description"]

    def test_discord_chat_system_prompt_requires_owner_name_placeholder(self):
        from core.brain import DISCORD_CHAT_SYSTEM_PROMPT

        assert "{owner_name}" in DISCORD_CHAT_SYSTEM_PROMPT

    def test_forward_to_owner_function_exists_and_renamed(self):
        import core.brain as brain

        assert hasattr(brain, "forward_to_owner")
        assert not hasattr(brain, "forward_to_mohamed")


class TestReliabilityCampaignFlags:
    """New flags added for the reliability campaign (see
    .claude/skills/aster-gemma-reliability-campaign). All default True except
    the tool-sampling knobs, which default to byte-identical copies of the
    main sampling knobs (a no-op until deliberately changed)."""

    def test_flags_default_true(self):
        assert config.RELIABILITY_LOG_ENABLED is True
        assert config.LOOP_GUARD_ENABLED is True
        assert config.TOOL_OUTPUT_COMPRESSION is True
        assert config.STRIP_THINK_TAGS is True
        assert config.EXACT_TOKEN_COUNT is True
        assert config.HYBRID_RECALL is True

    def test_tool_sampling_defaults_match_main_knobs(self):
        assert config.LLM_TOOL_TEMPERATURE == config.LLM_TEMPERATURE
        assert config.LLM_TOOL_TOP_P == config.LLM_TOP_P
        assert config.LLM_TOOL_TOP_K == config.LLM_TOP_K

    def test_execute_llm_completion_honors_explicit_tool_sampling(self, monkeypatch):
        """The admin loop's tool-round call site now passes
        config.LLM_TOOL_TEMPERATURE/_TOP_P/_TOP_K explicitly (core/brain.py) —
        verify _execute_llm_completion puts whatever it's given into the
        POST payload, so a divergent tool-sampling config actually takes
        effect rather than silently resolving back to the main knobs."""
        from unittest.mock import MagicMock, patch
        from core.brain import _execute_llm_completion

        monkeypatch.setattr(config, "LLM_TOOL_TEMPERATURE", 0.5)
        monkeypatch.setattr(config, "LLM_TOOL_TOP_P", 0.8)
        monkeypatch.setattr(config, "LLM_TOOL_TOP_K", 20)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            _execute_llm_completion(
                messages=[{"role": "user", "content": "hi"}],
                temperature=config.LLM_TOOL_TEMPERATURE,
                top_p=config.LLM_TOOL_TOP_P,
                top_k=config.LLM_TOOL_TOP_K,
            )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["temperature"] == 0.5
        assert payload["top_p"] == 0.8
        assert payload["top_k"] == 20
