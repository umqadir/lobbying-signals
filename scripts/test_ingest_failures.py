"""Fixture-based page retries, partial sweeps, and deferred CI failure tests.

Run: python scripts/test_ingest_failures.py (no live API).
"""

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db
import httpx


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest = load_module("ingest_failures", "01_ingest.py")
refresh = load_module("refresh_failures", "07_refresh.py")
FIXTURES = json.loads((ROOT / "scripts/fixtures/filing_details.json").read_text())


class IngestFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.enterContext(patch.object(db, "DB_PATH", self.path / "filings.db"))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.sleep = self.enterContext(patch.object(ingest.time, "sleep"))
        self.get = self.enterContext(patch.object(ingest.httpx, "get"))
        self.enterContext(patch.object(refresh.subprocess, "run"))
        self.enterContext(patch.object(ingest, "datetime", wraps=datetime)).now.return_value = datetime(2026, 10, 9)

    def response(self, status=200, raw=None, headers=None):
        if raw is None:
            raw = {"results": [FIXTURES["self_filer"]], "next": None}
        return httpx.Response(status, json=raw, headers=headers,
                              request=httpx.Request("GET", "https://lda.gov/api/v1/filings/"))

    def test_transient_errors_retry_then_return_fixture(self):
        errors = [self.response(429), self.response(500), self.response(503), self.response(504),
                  httpx.ReadTimeout("timed out"), httpx.ConnectError("connection reset"),
                  httpx.RemoteProtocolError("disconnected")]
        for error in errors:
            with self.subTest(error=error):
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.get.side_effect = [error, self.response()]
                data = ingest.fetch_filings_page(2026, None, posted_after="2026-10-02")
                self.assertEqual(data["results"], [FIXTURES["self_filer"]])
                self.assertEqual(self.get.call_count, 2)
                self.sleep.assert_called_once_with(5)
                kwargs = self.get.call_args.kwargs
                self.assertEqual(kwargs["params"]["filing_dt_posted_after"], "2026-10-02")
                self.assertNotIn("filing_type", kwargs["params"])
                self.assertLessEqual(kwargs["timeout"], 10)

    def test_retry_after_seconds_date_and_invalid_fallback(self):
        for status in (429, 503):
            for header, delay in (("12", 12), ("Fri, 09 Oct 2026 00:00:17 GMT", 17), ("invalid", 5)):
                with self.subTest(status=status, header=header):
                    self.sleep.reset_mock()
                    self.get.side_effect = [self.response(status, headers={"Retry-After": header}), self.response()]
                    with patch.object(ingest.datetime, "now", return_value=datetime(2026, 10, 9, tzinfo=timezone.utc)):
                        ingest.fetch_filings_page(2026, "Q3")
                    self.sleep.assert_called_once_with(delay)
                    self.assertEqual(self.get.call_args.kwargs["params"]["filing_type"], "Q3")

    def test_persistent_failure_preserves_error_and_does_not_sleep_after_last_attempt(self):
        self.get.return_value = self.response(503)
        with self.assertRaises(httpx.HTTPStatusError) as raised:
            ingest.fetch_filings_page(2026, None)
        self.assertEqual(raised.exception.response.status_code, 503)
        self.assertEqual(self.get.call_count, 5)
        self.assertEqual(self.sleep.call_args_list, [call(5), call(10), call(20), call(40)])

    def test_client_errors_and_excessive_retry_after_do_not_retry(self):
        for response in (self.response(400), self.response(403),
                         self.response(503, headers={"Retry-After": "3600"})):
            with self.subTest(status=response.status_code, headers=response.headers):
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.get.return_value = response
                with self.assertRaises(httpx.HTTPStatusError):
                    ingest.fetch_filings_page(2026, None)
                self.get.assert_called_once()
                self.sleep.assert_not_called()

    def test_retry_budget_includes_timeout_time_and_is_shared_between_years(self):
        clock = [0.0]
        initial_time = [0.0]

        def fail(*args, **kwargs):
            timeout = kwargs["timeout"]
            elapsed = 60 if timeout == 60 else timeout * 4
            clock[0] += elapsed
            if timeout == 60:
                initial_time[0] += elapsed
            raise httpx.ReadTimeout("timed out")

        self.get.side_effect = fail
        self.sleep.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        with patch.object(ingest.time, "monotonic", side_effect=lambda: clock[0]):
            result = ingest.ingest_posted_after("2026-10-02", start_year=2025)
        self.assertFalse(result["complete"])
        self.assertEqual(result["failed_years"], [2025, 2026])
        self.assertEqual(self.get.call_count, 6)
        self.assertLessEqual(clock[0] - initial_time[0], ingest.PAGE_RETRY_BUDGET_SECONDS)
        self.assertEqual(self.sleep.call_args_list, [call(5), call(10), call(20), call(5)])

    def test_successful_retry_also_consumes_shared_budget(self):
        clock = [0.0]
        responses = iter([self.response(503), self.response()])

        def respond(*args, **kwargs):
            if kwargs["timeout"] != 60:
                clock[0] += 10
            return next(responses)

        budget = {"remaining": 180}
        self.get.side_effect = respond
        self.sleep.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        with patch.object(ingest.time, "monotonic", side_effect=lambda: clock[0]):
            ingest.fetch_filings_page(2026, None, retry_budget=budget)
        self.assertEqual(budget["remaining"], 165)

    def test_partial_sweep_flushes_batch_recomputes_and_continues_next_year(self):
        first = copy.deepcopy(FIXTURES["self_filer"])
        first["filing_year"] = 2025
        amendment = dict(first, filing_uuid="partial-amendment", filing_type="2A", dt_posted="2026-10-08")
        self.get.side_effect = [self.response(raw={"results": [first, amendment], "next": "page2"}),
                               *[self.response(503)] * 5,
                               self.response(raw={"results": [FIXTURES["firm"]], "next": None})]
        result = ingest.ingest_posted_after("2026-10-02", start_year=2025)
        self.assertEqual(result, {"loaded": 3, "complete": False, "failed_years": [2025]})
        self.assertEqual([(c.kwargs["params"]["filing_year"], c.kwargs["params"]["page"])
                          for c in self.get.call_args_list], [(2025, 1)] + [(2025, 2)] * 5 + [(2026, 1)])
        with db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT is_current FROM filings WHERE sopr_filing_id=?",
                                         (first["filing_uuid"],)).fetchone()[0], 0)

    def test_empty_sweep_is_complete_and_latest_propagates_result(self):
        self.get.return_value = self.response(raw={"results": [], "next": None})
        result = ingest.ingest_latest()
        self.assertEqual(result, {"loaded": 0, "complete": True, "failed_years": []})
        self.assertEqual(self.get.call_count, 7)

    def run_refresh(self, complete, marker=None):
        calls = []
        source = SimpleNamespace(ingest_latest=lambda: calls.append("ingest") or
                                 {"loaded": 1, "complete": complete, "failed_years": [] if complete else [2025]})
        rules = SimpleNamespace(connect=lambda: Mock(), init_tables=lambda conn: None,
                                load_rules=lambda path: [], RULES_PATH="fixture",
                                process_batch=lambda **kwargs: calls.append("extract") or 1)
        trends = SimpleNamespace(export_json=lambda: calls.append("export"),
                                 export_clients_json=lambda: calls.append("reexport"))
        details = SimpleNamespace(backfill_details=lambda **kwargs: calls.append("backfill") or {"updated": 1})
        argv = ["07_refresh.py"] + (["--failure-marker", str(marker)] if marker is not None else [])
        with patch.object(refresh, "_load_module", side_effect=[source, rules, trends, details]), \
             patch.object(sys, "argv", argv):
            code = refresh.main()
        self.assertEqual(calls, ["ingest", "extract", "export", "backfill", "reexport"])
        return code

    def test_local_failure_is_reported_only_after_processing(self):
        self.assertEqual(self.run_refresh(False), 1)
        self.assertEqual(self.run_refresh(True), 0)

    def test_marker_defers_failure_and_success_clears_it(self):
        marker = self.path / "ingest-incomplete"
        self.assertEqual(self.run_refresh(False, marker), 0)
        self.assertIn("2025", marker.read_text())
        self.assertEqual(self.run_refresh(True, marker), 0)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
