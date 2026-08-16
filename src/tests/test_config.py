import unittest

from config import QbittorrentConfig


class QbittorrentConfigTest(unittest.TestCase):
    def test_default_url_includes_qbittorrent_default_port(self) -> None:
        config = QbittorrentConfig.from_env({})

        self.assertEqual(config.url, "http://localhost:8080")
        self.assertEqual(config.base_url, "http://localhost:8080/api/v2")

    def test_url_is_normalized_before_building_api_url(self) -> None:
        config = QbittorrentConfig.from_env(
            {
                "QBIT_URL": "https://qbit.example:8443/",
                "QBIT_USERNAME": "fixture",
                "QBIT_PASSWORD": "",
            }
        )

        self.assertEqual(config.url, "https://qbit.example:8443")
        self.assertEqual(config.base_url, "https://qbit.example:8443/api/v2")
        self.assertEqual(config.username, "fixture")
        self.assertEqual(config.password, "")

    def test_url_rejects_unsupported_components(self) -> None:
        invalid_urls = {
            "localhost:8080": "http or https scheme",
            "ftp://localhost:8080": "http or https scheme",
            "http://[::1": "valid URL",
            "http://:8080": "hostname",
            "http://user:@localhost:8080": "credentials",
            "http://localhost:8080/qbit": "path",
            "http://localhost:8080?filter=all": "query or fragment",
            "http://localhost:8080#status": "query or fragment",
            "http://local host:8080": "whitespace",
            "http://localhost:not-a-port": "invalid port",
        }

        for url, message in invalid_urls.items():
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, message):
                QbittorrentConfig(
                    url=url,
                    username="fixture",
                    password="",
                )


if __name__ == "__main__":
    unittest.main()
