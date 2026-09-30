import asyncio
import os
import signal
import socket
import time
import unittest
from unittest import mock

from starlette.testclient import TestClient

import config
from dashboard import COOKIE_NAME, Dashboard, EventLog
from monitor import Monitor
from tests.helpers import FakeNotifier, FakeSource, make_listing, patch_config, temp_storage

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("203.0.113.7", 50000)
JSON = {"X-Dashboard": "1", "Content-Type": "application/json"}


class DashboardTestCase(unittest.TestCase):
    public_url = ""

    def setUp(self):
        self.patcher = patch_config(DASHBOARD_PUBLIC_URL=self.public_url)
        self.patcher.start()
        self.store = temp_storage()
        self.monitor = Monitor(self.store, FakeNotifier(), {m: FakeSource(m) for m in config.EBAY_MARKETS})
        self.dashboard = Dashboard(self.store, self.monitor, EventLog())

    def tearDown(self):
        self.patcher.stop()

    def client(self, base_url="http://127.0.0.1:8765", client=LOCAL):
        return TestClient(self.dashboard.app, base_url=base_url, client=client, follow_redirects=False)


class LocalAccessTest(DashboardTestCase):
    def test_page_and_status(self):
        c = self.client()
        page = c.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Monitor annunci", page.text)
        status = c.get("/api/status").json()
        for key in ("paused", "quota", "interval", "markets", "keywords", "excludes", "stats", "recent", "events"):
            self.assertIn(key, status)
        self.assertEqual(len(status["markets"]), len(config.EBAY_MARKETS))
        self.assertFalse(status["public"])

    def test_security_headers(self):
        headers = self.client().get("/").headers
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertEqual(headers["cache-control"], "no-store")

    def test_unknown_host_rejected(self):
        self.assertEqual(self.client(base_url="http://evil.example").get("/api/status").status_code, 400)

    def test_localhost_from_other_machine_needs_login(self):
        self.assertEqual(self.client(client=REMOTE).get("/api/status").status_code, 401)

    def test_commands_need_anti_csrf_headers(self):
        c = self.client()
        self.assertEqual(c.post("/api/pause", json={"paused": True}).status_code, 403)
        r = c.post("/api/pause", content=b'{"paused": true}', headers={"X-Dashboard": "1", "Content-Type": "text/plain"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.store.paused)

    def test_pause_and_resume(self):
        c = self.client()
        self.assertEqual(c.post("/api/pause", json={"paused": True}, headers=JSON).status_code, 200)
        self.assertTrue(self.store.paused)
        c.post("/api/pause", json={"paused": False}, headers=JSON)
        self.assertFalse(self.store.paused)
        self.assertTrue(self.monitor._wake.is_set())

    def test_keywords(self):
        c = self.client()
        r = c.post("/api/keywords", json={"terms": "RTX 4090, Game Boy"}, headers=JSON).json()
        self.assertEqual(r["added"], ["RTX 4090", "Game Boy"])
        r = c.post("/api/keywords", json={"terms": "rtx4090"}, headers=JSON).json()
        self.assertEqual(r["existing"], ["rtx4090"])
        kid = self.store.keywords()[0].id
        self.assertEqual(c.delete(f"/api/keywords/{kid}", headers=JSON).json()["removed"], "RTX 4090")
        self.assertEqual(c.delete("/api/keywords/999", headers=JSON).status_code, 404)
        self.assertEqual(c.delete("/api/keywords/abc", headers=JSON).status_code, 404)

    def test_excludes(self):
        c = self.client()
        c.post("/api/excludes", json={"terms": "cover, custodia"}, headers=JSON)
        self.assertEqual([t for _, t in self.store.excludes()], ["cover", "custodia"])
        eid = self.store.excludes()[0][0]
        self.assertEqual(c.delete(f"/api/excludes/{eid}", headers=JSON).status_code, 200)

    def test_interval_is_clamped_and_validated(self):
        c = self.client()
        self.assertEqual(c.post("/api/interval", json={"seconds": 5}, headers=JSON).json()["interval"],
                         config.MIN_INTERVAL_SECONDS)
        self.assertEqual(c.post("/api/interval", json={"seconds": "x"}, headers=JSON).status_code, 400)

    def test_markets(self):
        c = self.client()
        c.post("/api/markets/EBAY_FR", json={"enabled": False}, headers=JSON)
        self.assertNotIn("EBAY_FR", self.store.enabled_markets)
        c.post("/api/markets/EBAY_FR", json={"enabled": True}, headers=JSON)
        self.assertIn("EBAY_FR", self.store.enabled_markets)
        self.assertEqual(c.post("/api/markets/EBAY_XX", json={"enabled": True}, headers=JSON).status_code, 404)

    def test_bad_requests(self):
        c = self.client()
        self.assertEqual(c.post("/api/pause", content=b"[1", headers=JSON).status_code, 400)
        self.assertEqual(c.post("/api/pause", content=b"[1, 2]", headers=JSON).status_code, 400)
        self.assertEqual(c.post("/api/pause", content=b"a" * 20_000, headers=JSON).status_code, 413)
        self.assertEqual(c.get("/nonesiste").status_code, 404)
        self.assertEqual(c.get("/api/pause").status_code, 405)

    def test_login_disabled_locally(self):
        self.assertEqual(self.client().get("/login?t=x").status_code, 404)

    def test_market_matches_in_status(self):
        self.monitor.health["EBAY_IT"].last_ok = time.time()
        self.monitor.health["EBAY_IT"].last_matches = 7
        markets = {m["id"]: m for m in self.client().get("/api/status").json()["markets"]}
        self.assertEqual(markets["EBAY_IT"]["last_matches"], 7)
        self.assertEqual(markets["EBAY_IT"]["state"], "ok")

    def fake_quota(self, remaining=4000, reset=None):
        calls = []

        async def fake_rate_limits(auth):
            calls.append(auth)
            return {"limit": 5000, "remaining": remaining, "count": 5000 - remaining, "reset": reset}

        self.dashboard.auth = object()
        return calls, mock.patch("dashboard.get_rate_limits", fake_rate_limits)

    def test_quota_is_read_from_ebay_even_while_paused(self):
        calls, patcher = self.fake_quota()
        with patcher:
            self.store.paused = True
            quota = self.client().get("/api/status").json()["quota"]
        self.assertEqual(quota["source"], "eBay")
        self.assertEqual(quota["remaining"], 4000)
        self.assertEqual(len(calls), 1)

    def test_quota_counts_calls_made_after_the_ebay_reading(self):
        calls, patcher = self.fake_quota(remaining=4000)
        with patcher:
            c = self.client()
            c.get("/api/status")
            self.store.add_api_calls(16)
            quota = c.get("/api/status").json()["quota"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(quota["remaining"], 3984)
        self.assertEqual(quota["used"], 1016)
        self.assertEqual(quota["since_fetch"], 16)

    def test_quota_is_reread_when_the_reset_time_passes(self):
        from datetime import datetime, timezone
        past = datetime.fromtimestamp(time.time() + 0.2, tz=timezone.utc)
        calls, patcher = self.fake_quota(reset=past)
        with patcher:
            c = self.client()
            c.get("/api/status")
            time.sleep(0.3)
            c.get("/api/status")
        self.assertEqual(len(calls), 2)

    def test_failed_quota_reading_keeps_the_last_good_value(self):
        calls, patcher = self.fake_quota(remaining=4200)
        with patcher:
            self.client().get("/api/status")

        async def broken(auth):
            return None

        self.dashboard._quota_at = 0
        with mock.patch("dashboard.get_rate_limits", broken):
            quota = self.client().get("/api/status").json()["quota"]
        self.assertEqual(quota["source"], "eBay")
        self.assertEqual(quota["remaining"], 4200)

    def test_status_content(self):
        self.store.add_keyword("RTX 4090")
        self.store.insert_seen(make_listing("1"), "fp", "RTX 4090")
        self.store.add_api_calls(10)
        status = self.client().get("/api/status").json()
        self.assertEqual(status["keywords"][0]["notified"], 1)
        self.assertEqual(status["stats"]["notified_today"], 1)
        self.assertEqual(status["quota"]["local_used"], 10)
        self.assertEqual(status["quota"]["source"], "stima locale")
        self.assertEqual(status["recent"][0]["source_name"], "eBay Italia")


class PublicAccessTest(DashboardTestCase):
    public_url = "https://monitor.example.com"
    base = "https://monitor.example.com"

    def login_path(self):
        return self.dashboard.create_login_link().replace(self.public_url, "")

    def test_without_login_everything_is_closed(self):
        c = self.client(base_url=self.base, client=REMOTE)
        page = c.get("/")
        self.assertEqual(page.status_code, 401)
        self.assertIn("Accesso richiesto", page.text)
        self.assertEqual(c.get("/api/status").status_code, 401)
        self.assertEqual(c.post("/api/pause", json={"paused": True}, headers=JSON).status_code, 401)
        self.assertFalse(self.store.paused)

    def test_login_link_flow(self):
        c = self.client(base_url=self.base, client=REMOTE)
        path = self.login_path()
        r = c.get(path)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/")
        cookie = r.headers["set-cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=2592000"):
            self.assertIn(flag.lower(), cookie.lower())
        self.assertEqual(c.get("/api/status").status_code, 200)
        self.assertEqual(c.post("/api/pause", json={"paused": True}, headers=JSON).status_code, 200)
        self.assertTrue(self.store.paused)
        self.assertEqual(self.client(base_url=self.base, client=REMOTE).get(path).status_code, 403)

    def test_invalid_and_expired_links(self):
        c = self.client(base_url=self.base, client=REMOTE)
        self.assertEqual(c.get("/login?t=inventato").status_code, 403)
        self.assertEqual(c.get("/login").status_code, 403)
        path = self.login_path()
        self.dashboard._login_tokens = {h: time.time() - 1 for h in self.dashboard._login_tokens}
        self.assertEqual(c.get(path).status_code, 403)

    def test_fake_cookie_rejected(self):
        c = self.client(base_url=self.base, client=REMOTE)
        c.cookies.set(COOKIE_NAME, "falso")
        self.assertEqual(c.get("/api/status").status_code, 401)

    def test_session_survives_restart_and_can_be_revoked(self):
        c = self.client(base_url=self.base, client=REMOTE)
        c.get(self.login_path())
        restarted = Dashboard(self.store, self.monitor, EventLog())
        c2 = TestClient(restarted.app, base_url=self.base, client=REMOTE, cookies=c.cookies)
        self.assertEqual(c2.get("/api/status").status_code, 200)
        self.assertEqual(restarted.revoke_sessions(), 1)
        self.assertEqual(c2.get("/api/status").status_code, 401)

    def test_local_access_still_works_through_ssh_tunnel(self):
        self.assertEqual(self.client().get("/api/status").status_code, 200)


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = self._free_port()
        self.patcher = patch_config(DASHBOARD_PORT=self.port)
        self.patcher.start()
        self.store = temp_storage()
        self.monitor = Monitor(self.store, FakeNotifier(), {})

    async def asyncTearDown(self):
        self.patcher.stop()

    @staticmethod
    def _free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def test_real_server_starts_serves_and_stops(self):
        import httpx
        d = Dashboard(self.store, self.monitor, EventLog())
        await d.start()
        async with httpx.AsyncClient(trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{self.port}/api/status")
        self.assertEqual(r.status_code, 200)
        await d.stop()
        self.assertTrue(d._task.done())

    async def test_busy_port_raises_oserror(self):
        with socket.socket() as busy:
            busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            busy.bind(("127.0.0.1", self.port))
            busy.listen()
            with self.assertRaises(OSError):
                await Dashboard(self.store, self.monitor, EventLog()).start()

    @unittest.skipIf(os.name == "nt", "segnali POSIX")
    async def test_ctrl_c_is_left_to_the_bot(self):
        d = Dashboard(self.store, self.monitor, EventLog())
        await d.start()
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        loop.add_signal_handler(signal.SIGINT, stop.set)
        try:
            loop.call_later(0.2, os.kill, os.getpid(), signal.SIGINT)
            await asyncio.wait_for(stop.wait(), timeout=3)
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            await d.stop()


if __name__ == "__main__":
    unittest.main()
