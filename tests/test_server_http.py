"""Dedicated HTTP endpoint tests for Joshua server using FastAPI TestClient."""

import os
import pytest
from unittest.mock import patch, MagicMock

# Must set token before importing server module
TOKEN = "test-token-abc123"
os.environ["JOSHUA_INTERNAL_TOKEN"] = TOKEN


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("httpx", reason="fastapi test client dependency missing")
    from fastapi.testclient import TestClient
    from joshua.server import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def clear_registry():
    from joshua import server
    server._registry.clear()
    yield
    server._registry.clear()


@pytest.fixture
def auth_headers():
    return {"X-Internal-Token": TOKEN}


@pytest.fixture
def minimal_config(tmp_path):
    return {
        "project": {"name": "test-project", "path": str(tmp_path)},
        "runner": {"type": "claude"},
        "agents": {"dev": {"skill": "dev"}},
        "sprint": {"max_cycles": 1, "cycle_sleep": 0},
    }


# ── /health ──────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "sprints_total" in data
        assert "sprints_running" in data

    def test_health_no_auth_required(self, client):
        """Health endpoint must be accessible without a token."""
        r = client.get("/health")
        assert r.status_code == 200


# ── Auth ─────────────────────────────────────────────────────────────

class TestAuth:
    def test_missing_token_returns_401(self, client, minimal_config):
        r = client.post("/sprints", json={"config": minimal_config})
        assert r.status_code == 401

    def test_wrong_token_returns_401(self, client, minimal_config):
        r = client.post(
            "/sprints",
            json={"config": minimal_config},
            headers={"X-Internal-Token": "wrong"},
        )
        assert r.status_code == 401

    def test_list_sprints_requires_auth(self, client):
        r = client.get("/sprints")
        assert r.status_code == 401

    def test_stop_sprint_requires_auth(self, client):
        r = client.post("/sprints/nonexistent/stop")
        assert r.status_code == 401


# ── POST /sprints ────────────────────────────────────────────────────

class TestStartSprint:
    @patch("joshua.server.threading.Thread")
    def test_start_sprint_success(self, mock_thread, client, auth_headers, minimal_config):
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        mock_thread.return_value = mock_t

        r = client.post(
            "/sprints",
            json={"config": minimal_config},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "sprint_id" in data
        assert data["project"] == "test-project"
        assert data["running"] is True

    def test_invalid_config_version_returns_422(self, client, auth_headers, minimal_config):
        r = client.post(
            "/sprints",
            json={"config": minimal_config, "config_version": "99"},
            headers=auth_headers,
        )
        assert r.status_code == 422
        assert "config_version" in r.text

    def test_missing_project_path_returns_422(self, client, auth_headers):
        config = {
            "project": {"name": "test", "path": "/tmp/this-path-definitely-does-not-exist-xyz"},
            "runner": {"type": "claude"},
            "agents": {"dev": {"skill": "dev"}},
        }
        r = client.post(
            "/sprints",
            json={"config": config},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_ssrf_callback_url_blocked(self, client, auth_headers, minimal_config):
        """Internal IP in callback_url must be rejected."""
        with patch("joshua.server.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 80))]):
            r = client.post(
                "/sprints",
                json={"config": minimal_config, "callback_url": "http://localhost/exfil"},
                headers=auth_headers,
            )
        assert r.status_code == 422

    def test_ssrf_callback_10x_blocked(self, client, auth_headers, minimal_config):
        with patch("joshua.server.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.1", 80))]):
            r = client.post(
                "/sprints",
                json={"config": minimal_config, "callback_url": "http://10.0.0.1/hook"},
                headers=auth_headers,
            )
        assert r.status_code == 422

    def test_callback_url_non_http_scheme_blocked(self, client, auth_headers, minimal_config):
        r = client.post(
            "/sprints",
            json={"config": minimal_config, "callback_url": "ftp://example.com/hook"},
            headers=auth_headers,
        )
        assert r.status_code == 422

    @patch("joshua.server.threading.Thread")
    @patch("joshua.server.socket.getaddrinfo")
    def test_callback_url_public_accepted(self, mock_dns, mock_thread, client, auth_headers, minimal_config):
        # Mock DNS to return a public IP
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        mock_thread.return_value = mock_t

        with patch("joshua.server.socket.getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
            r = client.post(
                "/sprints",
                json={"config": minimal_config, "callback_url": "https://webhook.example.com/hook"},
                headers=auth_headers,
            )
        assert r.status_code == 200

    def test_ssrf_172_16_blocked(self, client, auth_headers, minimal_config):
        """172.16.0.0/12 (RFC 1918) must be blocked."""
        with patch("joshua.server.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("172.16.0.1", 80))]):
            r = client.post(
                "/sprints",
                json={"config": minimal_config, "callback_url": "http://evil.com/hook"},
                headers=auth_headers,
            )
            assert r.status_code == 422

    def test_ssrf_dns_rebinding_blocked(self, client, auth_headers, minimal_config):
        """DNS resolving to loopback must be blocked."""
        with patch("joshua.server.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))]):
            r = client.post(
                "/sprints",
                json={"config": minimal_config, "callback_url": "http://public-looking.com/hook"},
                headers=auth_headers,
            )
            assert r.status_code == 422


# ── GET /sprints ──────────────────────────────────────────────────────

class TestListSprints:
    def test_list_sprints_empty(self, client, auth_headers):
        r = client.get("/sprints", headers=auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ── GET /sprints/{id} ─────────────────────────────────────────────────

class TestGetSprint:
    def test_get_nonexistent_sprint_404(self, client, auth_headers):
        r = client.get("/sprints/nonexistent-id", headers=auth_headers)
        assert r.status_code == 404

    @patch("joshua.server.threading.Thread")
    def test_get_sprint_after_start(self, mock_thread, client, auth_headers, minimal_config):
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        mock_thread.return_value = mock_t

        start_r = client.post(
            "/sprints",
            json={"config": minimal_config},
            headers=auth_headers,
        )
        assert start_r.status_code == 200
        sprint_id = start_r.json()["sprint_id"]

        r = client.get(f"/sprints/{sprint_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["sprint_id"] == sprint_id


# ── POST /sprints/{id}/stop ───────────────────────────────────────────

class TestStopSprint:
    def test_stop_nonexistent_sprint_404(self, client, auth_headers):
        r = client.post("/sprints/nonexistent-id/stop", headers=auth_headers)
        assert r.status_code == 404

    @patch("joshua.server.threading.Thread")
    def test_stop_running_sprint(self, mock_thread, client, auth_headers, minimal_config):
        mock_t = MagicMock()
        mock_t.is_alive.return_value = True
        mock_thread.return_value = mock_t

        start_r = client.post(
            "/sprints",
            json={"config": minimal_config},
            headers=auth_headers,
        )
        sprint_id = start_r.json()["sprint_id"]

        r = client.post(f"/sprints/{sprint_id}/stop", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["stopped"] is True
