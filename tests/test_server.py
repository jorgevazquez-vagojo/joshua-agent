"""Tests for Joshua HTTP server."""

import json
import time
import pytest
from unittest.mock import patch, MagicMock

from joshua.sprint import Sprint


class TestSprintHooks:
    """Test the new Sprint hooks (stop, callback, context_provider)."""

    @pytest.fixture
    def minimal_config(self, tmp_path):
        return {
            "project": {"name": "test", "path": str(tmp_path)},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
            "sprint": {"max_cycles": 1, "cycle_sleep": 0},
        }

    def test_stop_flag_default(self, minimal_config):
        sprint = Sprint(minimal_config)
        assert sprint._stop_requested is False

    def test_stop_sets_flag(self, minimal_config):
        sprint = Sprint(minimal_config)
        sprint.stop()
        assert sprint._stop_requested is True

    def test_on_cycle_complete_default_none(self, minimal_config):
        sprint = Sprint(minimal_config)
        assert sprint.on_cycle_complete is None

    def test_context_provider_default_none(self, minimal_config):
        sprint = Sprint(minimal_config)
        assert sprint.context_provider is None

    def test_context_provider_in_build_context(self, minimal_config):
        sprint = Sprint(minimal_config)
        sprint.context_provider = lambda cycle: f"knowledge for cycle {cycle}"
        sprint.cycle = 5
        ctx = sprint._build_context()
        assert ctx["external_context"] == "knowledge for cycle 5"

    def test_context_provider_error_handled(self, minimal_config):
        sprint = Sprint(minimal_config)
        sprint.context_provider = lambda cycle: 1 / 0  # raises ZeroDivisionError
        sprint.cycle = 1
        ctx = sprint._build_context()
        assert ctx["external_context"] == ""

    def test_no_context_provider_empty_string(self, minimal_config):
        sprint = Sprint(minimal_config)
        sprint.cycle = 1
        ctx = sprint._build_context()
        assert ctx["external_context"] == ""

    @patch("joshua.sprint.Sprint._run_cycle", return_value="GO")
    @patch("joshua.sprint.time.sleep")
    def test_stop_requested_breaks_loop(self, mock_sleep, mock_cycle, minimal_config):
        minimal_config["sprint"]["max_cycles"] = 100
        sprint = Sprint(minimal_config)
        sprint._stop_requested = True
        sprint.run()
        # Should not have run any cycles since stop was already requested
        mock_cycle.assert_not_called()

    @patch("joshua.sprint.Sprint._run_cycle", return_value="GO")
    @patch("joshua.sprint.time.sleep")
    def test_on_cycle_complete_called(self, mock_sleep, mock_cycle, minimal_config):
        callback = MagicMock()
        sprint = Sprint(minimal_config)
        sprint.on_cycle_complete = callback
        sprint.run()
        callback.assert_called_once()
        call_data = callback.call_args[0][0]
        assert call_data["cycle"] == 1
        assert call_data["verdict"] == "GO"
        assert "stats" in call_data
        assert "timestamp" in call_data

    @patch("joshua.sprint.Sprint._run_cycle", return_value="GO")
    @patch("joshua.sprint.time.sleep")
    def test_on_cycle_complete_error_handled(self, mock_sleep, mock_cycle, minimal_config):
        def bad_callback(data):
            raise RuntimeError("callback exploded")
        sprint = Sprint(minimal_config)
        sprint.on_cycle_complete = bad_callback
        # Should not crash even though callback raises
        sprint.run()
        mock_cycle.assert_called_once()


class TestHubCallback:
    """Test Brain callback integration."""

    def test_setup_disabled(self):
        from joshua.integrations.hub_callback import setup_hub_integration
        sprint = MagicMock()
        sprint.on_cycle_complete = None
        sprint.context_provider = None
        config = {"integrations": {"hub": {"enabled": False}}}
        setup_hub_integration(sprint, config)
        assert sprint.on_cycle_complete is None

    def test_setup_no_integrations(self):
        from joshua.integrations.hub_callback import setup_hub_integration
        sprint = MagicMock()
        sprint.on_cycle_complete = None
        sprint.context_provider = None
        setup_hub_integration(sprint, {})
        assert sprint.on_cycle_complete is None

    def test_setup_enabled(self):
        from joshua.integrations.hub_callback import setup_hub_integration
        sprint = MagicMock()
        sprint.on_cycle_complete = None
        sprint.context_provider = None
        config = {
            "integrations": {
                "hub": {
                    "enabled": True,
                    "api_url": "http://localhost:4000",
                    "group_id": "abc-123",
                    "api_token": "secret",
                    "department": "engineering",
                }
            }
        }
        setup_hub_integration(sprint, config)
        assert sprint.on_cycle_complete is not None
        assert sprint.context_provider is not None

    def test_setup_no_group_id_skips(self):
        from joshua.integrations.hub_callback import setup_hub_integration
        sprint = MagicMock()
        sprint.on_cycle_complete = None
        config = {
            "integrations": {
                "hub": {
                    "enabled": True,
                    "api_url": "http://localhost:4000",
                }
            }
        }
        setup_hub_integration(sprint, config)
        # Should not set callbacks without group_id
        assert sprint.on_cycle_complete is None

    @patch("joshua.integrations.hub_callback.requests.post")
    def test_callback_posts_to_brain(self, mock_post):
        from joshua.integrations.hub_callback import HubCallback
        mock_post.return_value = MagicMock(status_code=200)
        cb = HubCallback("http://localhost:4000", "group-123", "token")
        cb.on_cycle_complete({"cycle": 1, "verdict": "GO"})
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "/api/sprints/callback" in url

    @patch("joshua.integrations.hub_callback.requests.post", side_effect=Exception("network"))
    def test_callback_handles_error(self, mock_post):
        from joshua.integrations.hub_callback import HubCallback
        cb = HubCallback("http://localhost:4000", "group-123")
        # Should not raise
        cb.on_cycle_complete({"cycle": 1, "verdict": "GO"})

    @patch("joshua.integrations.hub_callback.requests.get")
    def test_context_provider_fetches(self, mock_get):
        from joshua.integrations.hub_callback import HubContextProvider
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"title": "Test", "content": "Knowledge content"}],
        )
        provider = HubContextProvider("http://localhost:4000", "group-123", cache_ttl=0)
        ctx = provider.get_context(1)
        assert "BRAIN KNOWLEDGE" in ctx
        assert "Test" in ctx

    @patch("joshua.integrations.hub_callback.requests.get")
    def test_context_provider_caches(self, mock_get):
        from joshua.integrations.hub_callback import HubContextProvider
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"title": "Cached", "content": "Data"}],
        )
        provider = HubContextProvider("http://localhost:4000", "group-123", cache_ttl=300)
        ctx1 = provider.get_context(1)
        ctx2 = provider.get_context(2)
        # Should only call API once due to cache
        assert mock_get.call_count == 1
        assert ctx1 == ctx2
