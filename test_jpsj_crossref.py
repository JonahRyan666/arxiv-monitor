import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("FEISHU_WEBHOOK_URL", "https://example.invalid")

import arxiv_daily_report as monitor


def crossref_response(status_code, items=None, retry_after=None):
    result = Mock(status_code=status_code, text="")
    result.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    result.json.return_value = {"message": {"items": items or []}}
    if status_code >= 400:
        result.raise_for_status.side_effect = monitor.requests.HTTPError(f"HTTP {status_code}")
    return result


def item(doi, title, day, abstract=""):
    result = {
        "DOI": doi,
        "title": [title],
        "published-online": {"date-parts": [[day.year, day.month, day.day]]},
    }
    if abstract:
        result["abstract"] = abstract
    return result


class JpsjCrossrefTests(unittest.TestCase):
    def setUp(self):
        monitor.CROSSREF_RETRIES = 0
        monitor.CROSSREF_QUERY_DELAY = 0

    @patch.object(monitor.time, "sleep")
    @patch.object(monitor.requests, "get")
    def test_two_issn_requests_then_local_filter_and_dedup(self, get, _sleep):
        today = datetime.now(timezone.utc)
        relevant = item(
            "10.7566/JPSJ.95.123456",
            "Quantum spin liquid in a kagome magnet",
            today,
            "<jats:p>Neutron scattering reveals frustrated magnetism.</jats:p>",
        )
        irrelevant = item("10.7566/JPSJ.95.999999", "Electrical transport in a metal", today)
        get.side_effect = [
            crossref_response(200, [relevant, irrelevant]),
            crossref_response(200, [relevant]),
        ]

        papers = monitor.fetch_jpsj_crossref_papers(today - timedelta(days=90))

        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["id"], "jpsj:10.7566/jpsj.95.123456")
        requested_urls = [call.args[0] for call in get.call_args_list]
        self.assertIn("0031-9015", requested_urls[0])
        self.assertIn("1347-4073", requested_urls[1])
        self.assertNotIn("query.bibliographic", get.call_args_list[0].kwargs["params"])

    @patch.object(monitor.time, "sleep")
    @patch.object(monitor.requests, "get")
    def test_429_uses_retry_after(self, get, sleep):
        monitor.CROSSREF_RETRIES = 1
        get.side_effect = [
            crossref_response(429, retry_after="3"),
            crossref_response(200),
            crossref_response(200),
        ]

        monitor.fetch_jpsj_crossref_papers(datetime.now(timezone.utc) - timedelta(days=90))

        self.assertEqual(get.call_count, 3)
        sleep.assert_any_call(3.0)


if __name__ == "__main__":
    unittest.main()
