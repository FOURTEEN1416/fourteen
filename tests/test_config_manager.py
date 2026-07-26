from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from observability.config_manager import ConfigManager


def _write_config(path: Path) -> None:
    path.write_text(
        """env: test
llm:
  provider: deepseek
  model: deepseek-chat
  primary_model: deepseek-chat
  api_key: ${DEEPSEEK_API_KEY}
  api_base: https://api.deepseek.com/v1
  temperature: 0.7
  max_tokens: 2048
  cache:
    enabled: true
    ttl: 600
fusion:
  rag:
    use_shisi_rag: true
voice:
  enabled: true
  mimo-tts:
    api_key: ${MIMO_API_KEY}
custom_extension:
  nested:
    keep_me: yes
""",
        encoding="utf-8",
    )


def test_save_preserves_unknown_sections_placeholders_and_masked_secrets(tmp_path, monkeypatch):
    config_path = tmp_path / "system.yaml"
    _write_config(config_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "runtime-secret")
    monkeypatch.setenv("MIMO_API_KEY", "runtime-mimo-secret")

    manager = ConfigManager(str(tmp_path))
    assert manager.config.llm.api_key == "runtime-secret"

    manager.save({
        "llm": {
            "api_key": "****",
            "temperature": 0.9,
            "cache": {"enabled": False, "ttl": 3600},
        }
    })

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["llm"]["api_key"] == "${DEEPSEEK_API_KEY}"
    assert persisted["voice"]["mimo-tts"]["api_key"] == "${MIMO_API_KEY}"
    assert persisted["fusion"]["rag"]["use_shisi_rag"] is True
    assert persisted["custom_extension"]["nested"]["keep_me"] is True
    assert persisted["llm"]["temperature"] == 0.9
    assert persisted["llm"]["cache"] == {"enabled": False, "ttl": 3600}


def test_invalid_update_is_not_written(tmp_path):
    config_path = tmp_path / "system.yaml"
    _write_config(config_path)
    before = config_path.read_bytes()
    manager = ConfigManager(str(tmp_path))

    with pytest.raises(ValueError):
        manager.save({"llm": {"temperature": 99}})

    assert config_path.read_bytes() == before


def test_get_config_dict_keeps_extensions_and_resolves_only_returned_copy(tmp_path, monkeypatch):
    config_path = tmp_path / "system.yaml"
    _write_config(config_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "resolved-secret")
    manager = ConfigManager(str(tmp_path))

    effective = manager.get_config_dict()
    raw = manager.get_config_dict(resolve_env=False)

    assert effective["llm"]["api_key"] == "resolved-secret"
    assert raw["llm"]["api_key"] == "${DEEPSEEK_API_KEY}"
    assert effective["custom_extension"]["nested"]["keep_me"] is True


def test_save_callback_runs_after_unlock(tmp_path):
    _write_config(tmp_path / "system.yaml")
    manager = ConfigManager(str(tmp_path))
    observed: list[float] = []

    def callback(_old, _new):
        observed.append(manager.get_config_dict()["llm"]["temperature"])

    manager.on_change(callback)
    manager.save({"llm": {"temperature": 0.6}})

    assert observed == [0.6]
