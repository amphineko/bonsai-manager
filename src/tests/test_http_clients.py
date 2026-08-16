import unittest
from datetime import timedelta
from typing import TYPE_CHECKING, cast, override
from unittest.mock import Mock, call, patch

from config import BangumiConfig, QbittorrentConfig
from lib.bangumi import BangumiClient
from lib.http_client import (
    DEFAULT_HTTP_TIMEOUT,
    RETRY_STATUS_CODES,
    create_retrying_session,
)
from lib.qbittorrent import QbittorrentClient

if TYPE_CHECKING:
    from requests.adapters import HTTPAdapter


class RetryingSessionTest(unittest.TestCase):
    def test_retries_idempotent_requests_on_transient_statuses(self) -> None:
        session = create_retrying_session()
        self.addCleanup(session.close)

        for prefix in ("http://", "https://"):
            adapter = cast("HTTPAdapter", session.get_adapter(prefix))
            retry = adapter.max_retries
            self.assertEqual(retry.total, 3)
            self.assertEqual(retry.backoff_factor, 0.5)
            self.assertEqual(retry.status_forcelist, RETRY_STATUS_CODES)
            self.assertTrue(retry.respect_retry_after_header)
            self.assertFalse(retry.raise_on_status)
            allowed_methods = retry.allowed_methods
            if allowed_methods is None:
                self.fail("Retries must be limited to idempotent methods.")
            self.assertIn("GET", allowed_methods)
            self.assertNotIn("POST", allowed_methods)


class BangumiHttpTest(unittest.TestCase):
    client: BangumiClient

    @override
    def setUp(self) -> None:
        self.client = BangumiClient(
            BangumiConfig(
                base_url="https://example.invalid",
                user_agent="test",
                token=None,
                username=None,
                collection_ttl=timedelta(hours=6),
            )
        )

    @override
    def tearDown(self) -> None:
        self.client.close()

    def test_subject_request_uses_default_timeout(self) -> None:
        response = Mock()
        response.json.return_value = dict[str, object]()

        with patch.object(self.client.session, "get", return_value=response) as get:
            self.client.get_subject(123)

        get.assert_called_once_with(
            "https://example.invalid/v0/subjects/123",
            timeout=DEFAULT_HTTP_TIMEOUT,
        )


class QbittorrentHttpTest(unittest.TestCase):
    client: QbittorrentClient

    @override
    def setUp(self) -> None:
        self.client = QbittorrentClient(
            QbittorrentConfig(
                host="https://example.invalid",
                port=443,
                username="fixture",
                password="secret",
            )
        )

    @override
    def tearDown(self) -> None:
        self.client.close()

    def test_login_uses_default_timeout(self) -> None:
        response = Mock()

        with patch.object(self.client.session, "post", return_value=response) as post:
            self.client.login()

        post.assert_called_once_with(
            "https://example.invalid:443/api/v2/auth/login",
            data={"username": "fixture", "password": "secret"},
            timeout=DEFAULT_HTTP_TIMEOUT,
        )

    def test_torrent_requests_use_default_timeout(self) -> None:
        response = Mock()
        response.json.return_value = list[object]()

        with patch.object(self.client.session, "get", return_value=response) as get:
            self.client.get_all_torrents()
            self.client.get_torrents_info(["a" * 40, "b" * 40])
            self.client.get_torrent_files("a" * 40)

        self.assertEqual(
            get.call_args_list,
            [
                call(
                    "https://example.invalid:443/api/v2/torrents/info",
                    timeout=DEFAULT_HTTP_TIMEOUT,
                ),
                call(
                    "https://example.invalid:443/api/v2/torrents/info",
                    params={"hashes": f"{'a' * 40}|{'b' * 40}"},
                    timeout=DEFAULT_HTTP_TIMEOUT,
                ),
                call(
                    "https://example.invalid:443/api/v2/torrents/files",
                    params={"hash": "a" * 40},
                    timeout=DEFAULT_HTTP_TIMEOUT,
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
