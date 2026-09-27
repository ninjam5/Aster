"""
Tests for first_run_setup.py's pure functions (no input() involved).

Exercises the file-writing logic (render_secrets_yaml, write_secrets,
load_existing_secrets, update_identity) against tmp_path scratch copies —
never touches the real secrets.yaml / self_config.yaml.
"""
import os
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import first_run_setup as wizard

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRenderAndLoadSecrets:
    def test_render_fills_defaults_for_missing_keys(self):
        rendered = wizard.render_secrets_yaml({"spotify_client_id": "abc123"})
        parsed = yaml.safe_load(rendered)
        assert parsed["spotify"]["client_id"] == "abc123"
        assert parsed["spotify"]["redirect_uri"] == wizard.DEFAULT_SPOTIFY_REDIRECT_URI
        assert parsed["telegram"]["bot_token"] == ""
        assert parsed["telegram"]["authorized_chat_id"] == 0
        assert parsed["firecrawl"]["api_key"] == ""

    def test_render_produces_valid_yaml_with_special_characters(self):
        rendered = wizard.render_secrets_yaml({"discord_bot_token": 'weird"quote'})
        parsed = yaml.safe_load(rendered)
        assert parsed["discord"]["bot_token"] == 'weird"quote'

    def test_write_then_load_round_trip(self, tmp_path):
        secrets_path = tmp_path / "secrets.yaml"
        values = {
            "spotify_client_id": "id1",
            "spotify_client_secret": "secret1",
            "telegram_bot_token": "111:AAA",
            "telegram_chat_id": 555,
            "discord_bot_token": "ddd.eee.fff",
            "livekit_url": "wss://example.livekit.cloud",
            "livekit_api_key": "APIkey",
            "livekit_api_secret": "APIsecret",
            "firecrawl_api_key": "fc-real",
        }
        wizard.write_secrets(values, path=secrets_path)
        loaded = wizard.load_existing_secrets(path=secrets_path)
        assert loaded == values | {"spotify_redirect_uri": wizard.DEFAULT_SPOTIFY_REDIRECT_URI}

    def test_load_existing_secrets_missing_file_returns_empty_dict(self, tmp_path):
        assert wizard.load_existing_secrets(path=tmp_path / "nope.yaml") == {}

    def test_load_existing_secrets_malformed_file_returns_empty_dict(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: [valid", encoding="utf-8")
        assert wizard.load_existing_secrets(path=bad) == {}


class TestUpdateIdentity:
    def test_preserves_comments_and_untouched_fields(self, tmp_path):
        scratch = tmp_path / "self_config.yaml"
        shutil.copyfile(REPO_ROOT / "self_config.yaml", scratch)
        original_comment_lines = [l for l in scratch.read_text(encoding="utf-8").splitlines() if l.strip().startswith("#")]

        wizard.update_identity(
            "TestAgent", "TestOwner", ["alice", "bob"],
            path=scratch, example_path=REPO_ROOT / "self_config.example.yaml",
        )

        new_text = scratch.read_text(encoding="utf-8")
        new_comment_lines = [l for l in new_text.splitlines() if l.strip().startswith("#")]
        assert new_comment_lines == original_comment_lines

        parsed = yaml.safe_load(new_text)
        assert parsed["identity"]["name"] == "TestAgent"
        assert parsed["identity"]["owner"] == "TestOwner"
        assert parsed["contacts"]["female_names"] == ["alice", "bob"]
        assert parsed["identity"]["version"] == "1.0"  # untouched field survives
        assert parsed["runtime"]["llm_model"] == "Qwen3.6-35B-A3B-UD-IQ4_XS"  # untouched section survives

    def test_two_space_indented_list_survives_round_trip(self, tmp_path):
        """Regression (2026-07-11): hand-edited configs indent list items with
        2 spaces (`  - name`), which the old consume-regex (4-space only)
        left dangling under the replacement list — producing invalid YAML
        ("expected <block end>, but found '-')."""
        scratch = tmp_path / "self_config.yaml"
        scratch.write_text(
            "identity:\n"
            "  name: Aster\n"
            "  owner: Old\n"
            "\n"
            "contacts:\n"
            "  # comment above the list\n"
            "  female_names:\n"
            "  - farah\n"
            "  - emily\n"
            "\n"
            "runtime:\n"
            "  llm_model: Qwen3.6-35B-A3B-UD-IQ4_XS\n",
            encoding="utf-8",
        )
        wizard.update_identity(
            "Aster", "New", ["alice"],
            path=scratch, example_path=REPO_ROOT / "self_config.example.yaml",
        )
        new_text = scratch.read_text(encoding="utf-8")
        parsed = yaml.safe_load(new_text)  # must not raise
        assert parsed["contacts"]["female_names"] == ["alice"]
        assert parsed["runtime"]["llm_model"] == "Qwen3.6-35B-A3B-UD-IQ4_XS"  # section after the list survives
        assert "# comment above the list" in new_text

    def test_empty_female_names_produces_empty_list(self, tmp_path):
        scratch = tmp_path / "self_config.yaml"
        shutil.copyfile(REPO_ROOT / "self_config.example.yaml", scratch)
        wizard.update_identity("Aster", "Solo", [], path=scratch, example_path=REPO_ROOT / "self_config.example.yaml")
        parsed = yaml.safe_load(scratch.read_text(encoding="utf-8"))
        assert parsed["contacts"]["female_names"] == []

    def test_seeds_from_example_when_target_missing(self, tmp_path):
        scratch = tmp_path / "self_config.yaml"
        assert not scratch.exists()
        wizard.update_identity("Aster", "Fresh", [], path=scratch, example_path=REPO_ROOT / "self_config.example.yaml")
        assert scratch.exists()
        parsed = yaml.safe_load(scratch.read_text(encoding="utf-8"))
        assert parsed["identity"]["owner"] == "Fresh"

    def test_blank_agent_and_owner_fall_back_to_defaults(self, tmp_path):
        scratch = tmp_path / "self_config.yaml"
        shutil.copyfile(REPO_ROOT / "self_config.example.yaml", scratch)
        wizard.update_identity("   ", "   ", [], path=scratch, example_path=REPO_ROOT / "self_config.example.yaml")
        parsed = yaml.safe_load(scratch.read_text(encoding="utf-8"))
        assert parsed["identity"]["name"] == "Aster"
        assert parsed["identity"]["owner"] == "User"


class TestYamlScalarQuoting:
    def test_plain_name_is_not_quoted(self):
        assert wizard._yaml_scalar("Mohamed") == "Mohamed"

    def test_empty_string_is_quoted(self):
        assert wizard._yaml_scalar("") == '""'

    def test_colon_containing_value_is_quoted(self):
        result = wizard._yaml_scalar("weird:name")
        assert result.startswith('"') and result.endswith('"')


class TestPollTelegramChatId:
    def test_invalid_token_returns_none_without_hanging(self):
        import time

        start = time.monotonic()
        result = wizard.poll_telegram_chat_id("invalid_fake_token", timeout_seconds=3, poll_interval=1)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 10
