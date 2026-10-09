"""Fixture-based detail parsing, migration, and resumable backfill regression tests.

Run: python scripts/test_ingest_details.py
Fixtures are synthetic and tests do not use the live API.
"""

import copy
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db
import httpx

spec = importlib.util.spec_from_file_location("ingest", ROOT / "01_ingest.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)
FIXTURES = json.loads((ROOT / "scripts/fixtures/filing_details.json").read_text())


class DetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.path / "filings.db")
        self.db_patch.start()
        with redirect_stdout(io.StringIO()):
            db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def load(self, key="self_filer", **changes):
        raw = copy.deepcopy(FIXTURES[key])
        raw.update(changes)
        parsed = ingest.parse_api_filing(raw)
        ingest.load_filings_to_db([parsed])
        return raw

    def response(self, raw, status=200, headers=None):
        return httpx.Response(status, json=raw, headers=headers,
                              request=httpx.Request("GET", "https://lda.gov/api/v1/filings/"))

    def run_backfill(self, responses, **kwargs):
        with patch.object(ingest.httpx, "Client") as client, patch.object(ingest.time, "sleep"):
            get = client.return_value.__enter__.return_value.get
            get.side_effect = responses
            with redirect_stdout(io.StringIO()) as output:
                stats = ingest.backfill_details(**kwargs)
            self.backfill_output = output.getvalue()
            return stats, get

    def test_self_filer_and_nested_lobbyists(self):
        parsed = ingest.parse_api_filing(FIXTURES["self_filer"])
        self.assertEqual(parsed["income"], 1250000)
        self.assertEqual(parsed["expenses"], 1250000)
        self.assertTrue(parsed["is_self_filer"])
        self.assertEqual(parsed["expenses_method"], "a")
        self.assertTrue(parsed["details_complete"])
        lobbyists = parsed["activities"][0]["lobbyists"]
        self.assertEqual(lobbyists[0], {
            "lda_lobbyist_id": 63767, "name": "Jane Q. Doe Jr.",
            "covered_position": "Chief of Staff, House Appropriations Committee", "is_new": True})
        self.assertIsNone(lobbyists[1]["covered_position"])
        self.assertFalse(lobbyists[1]["is_new"])
        self.assertEqual(len(parsed["activities"]), 1)
        self.assertEqual(len(parsed["lobbyist_activities"]), 2)

    def test_firm_amendment(self):
        parsed = ingest.parse_api_filing(FIXTURES["firm"])
        self.assertFalse(parsed["is_self_filer"])
        self.assertEqual(parsed["income"], 40000)
        self.assertIsNone(parsed["expenses"])
        self.assertIsNone(parsed["expenses_method"])
        self.assertEqual(parsed["filing_type"], "2A")
        self.load("firm")
        with db.get_db() as conn:
            self.assertEqual(conn.execute("SELECT is_self_filer FROM filings").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filing_lobbyists").fetchone()[0], 1)

    def test_zero_expenses_and_unknown_amounts(self):
        raw = dict(FIXTURES["self_filer"], expenses=0)
        self.assertTrue(ingest.parse_api_filing(raw)["is_self_filer"])
        raw.update(expenses=None, income=0)
        self.assertFalse(ingest.parse_api_filing(raw)["is_self_filer"])
        raw["income"] = None
        self.assertIsNone(ingest.parse_api_filing(raw)["is_self_filer"])

    def test_load_refetch_is_idempotent_preserves_income_and_activities(self):
        self.load()
        self.load(income="999", expenses=None, expenses_method="b")
        with db.get_db() as conn:
            f = conn.execute("SELECT * FROM filings").fetchone()
            self.assertEqual(f["income"], 1250000)
            self.assertEqual(f["expenses_method"], "b")
            self.assertEqual(f["is_self_filer"], 0)
            self.assertIsNotNone(f["details_fetched_at"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0], 1)
            people = conn.execute("SELECT * FROM filing_lobbyists ORDER BY activity_index, lobbyist_index").fetchall()
            self.assertEqual(len(people), 3)
            self.assertIsNotNone(people[0]["activity_id"])
            self.assertIsNone(people[-1]["activity_id"])

    def test_refetch_reordered_activities_matches_content(self):
        self.load()
        raw = copy.deepcopy(FIXTURES["self_filer"])
        raw["lobbying_activities"].reverse()
        ingest.load_filings_to_db([ingest.parse_api_filing(raw)])
        with db.get_db() as conn:
            people = conn.execute("SELECT activity_index, activity_id FROM filing_lobbyists ORDER BY activity_index").fetchall()
            self.assertIsNone(people[0]["activity_id"])
            self.assertIsNotNone(people[-1]["activity_id"])

    def test_legacy_migration_and_idempotence(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        legacy_schema = db.SCHEMA.replace("    expenses_method TEXT,\n", "").replace(
            "    is_self_filer INTEGER CHECK (is_self_filer IN (0, 1)),\n", "").replace(
            "    details_fetched_at TEXT,\n", "")
        conn.executescript(legacy_schema)
        conn.executemany("INSERT INTO registrants (id,name) VALUES (?,?)",
                         [(1, "Example Organization Inc."), (2, "Outside Firm LLP"), (3, "")])
        conn.execute("INSERT INTO clients (id,name) VALUES (1,'Example Organization')")
        conn.executemany("INSERT INTO filings (id,registrant_id,client_id,year,quarter,expenses) VALUES (?,?,1,2025,2,?)",
                         [(1,1,None), (2,2,None), (3,2,0), (4,3,None), (5,None,None)])
        with redirect_stdout(io.StringIO()):
            db._migrate_schema(conn)
            db._migrate_schema(conn)
        self.assertEqual([r[0] for r in conn.execute("SELECT is_self_filer FROM filings ORDER BY id")],
                         [1,0,1,None,None])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM migrations WHERE name='self_filer_v1'").fetchone()[0],1)
        conn.execute("UPDATE filings SET is_self_filer=0 WHERE id=1")
        db._migrate_schema(conn)
        self.assertEqual(conn.execute("SELECT is_self_filer FROM filings WHERE id=1").fetchone()[0],0)
        conn.close()

    def test_dashboard_uuid_set_all_quarter_filings_and_variants(self):
        clients_path = self.path / "clients.json"
        clients_path.write_text(json.dumps({"frames": {
            "quarter": {"current_quarter":{"year":2026,"quarter":2},
                        "baseline_quarter":{"year":2025,"quarter":2},
                        "risers":[{"key":"EXAMPLE ORGANIZATION", "examples":[{"uuid":"current"}]}],
                        "fallers":[], "new_entrants":[]},
            "qtd": {"current_quarter":{"year":2026,"quarter":3},
                    "baseline_quarter":{"year":2025,"quarter":3},
                    "risers":[], "fallers":[{"key":"BASELINE ONLY"}],
                    "new_entrants":[{"key":"NEW ENTRANT"}]}}}))
        with db.get_db() as conn:
            conn.executemany("INSERT INTO clients (id,name) VALUES (?,?)", [(1,"Example Organization Inc."),
                (2,"Example Organization"), (3,"Baseline Only LLC"), (4,"Unrelated"), (5,"New Entrant Inc.")])
            records = [('current',1,2026,2,1),('original',2,2026,2,0),('baseline',2,2025,2,1),
                       ('qtd-baseline-only',3,2025,3,1),('qtd-late',3,2026,3,1),('wrong-quarter',1,2026,1,1),
                       ('not-qtd-mover',1,2026,3,1),('unrelated',4,2026,2,1),('entrant',5,2026,3,1)]
            conn.executemany("INSERT INTO filings (sopr_filing_id,client_id,year,quarter,is_current) VALUES (?,?,?,?,?)", records)
            actual = ingest.dashboard_filing_uuids(conn, clients_path)
        self.assertEqual(actual, {'current','original','baseline','qtd-baseline-only','qtd-late','entrant'})

    def test_backfill_cap_and_resume_including_null_method(self):
        raw = self.load("firm")
        with db.get_db() as conn:
            conn.execute("UPDATE filings SET details_fetched_at=NULL")
            conn.commit()
        uuid = raw["filing_uuid"]
        stats, get = self.run_backfill([self.response(raw)], uuids=[uuid], max_requests=1)
        self.assertEqual(stats["requests"],1)
        self.assertEqual(stats["updated"],1)
        self.assertTrue(get.call_args.args[0].startswith("https://lda.gov/"))
        stats, get = self.run_backfill([], uuids=[uuid], max_requests=1)
        self.assertEqual(stats["eligible"],0)
        get.assert_not_called()

    def test_retry_counts_toward_cap_and_leaves_pending(self):
        raw = self.load()
        with db.get_db() as conn:
            conn.execute("UPDATE filings SET details_fetched_at=NULL")
            conn.commit()
        stats, get = self.run_backfill([self.response({},429,{'Retry-After':'5'})],
                                      uuids=[raw['filing_uuid']],max_requests=1)
        self.assertEqual(stats['requests'],1)
        self.assertEqual(stats['updated'],0)
        with db.get_db() as conn:
            self.assertIsNone(conn.execute('SELECT details_fetched_at FROM filings').fetchone()[0])

    def test_retry_5xx_then_success(self):
        raw = self.load()
        with db.get_db() as conn:
            conn.execute("UPDATE filings SET details_fetched_at=NULL")
            conn.commit()
        stats, _ = self.run_backfill([self.response({},503),self.response(raw)],
                                    uuids=[raw['filing_uuid']],max_requests=2)
        self.assertEqual(stats['requests'],2)
        self.assertEqual(stats['updated'],1)

    def test_persistent_outage_is_nonfatal(self):
        raw = self.load()
        with db.get_db() as conn:
            conn.execute("UPDATE filings SET details_fetched_at=NULL")
            conn.commit()
        stats, _ = self.run_backfill([self.response({},503)]*3, uuids=[raw['filing_uuid']])
        self.assertEqual(stats['errors'],1)
        self.assertEqual(stats['requests'],3)
        self.assertEqual(stats['updated'],0)

    def test_partial_payload_not_marked_complete(self):
        raw = self.load()
        raw['lobbying_activities'][0].pop('lobbyists')
        with db.get_db() as conn:
            conn.execute("UPDATE filings SET details_fetched_at=NULL")
            conn.commit()
        stats, _ = self.run_backfill([self.response(raw)]*3, uuids=[raw['filing_uuid']])
        self.assertEqual(stats['updated'],0)
        self.assertEqual(stats['errors'],1)

    def test_drift_audit_refetch_captures_details(self):
        raw = self.load()
        raw['expenses_method'] = 'c'
        with patch.object(ingest.httpx, 'get', return_value=self.response(raw)), patch.object(ingest.time,'sleep'):
            with redirect_stdout(io.StringIO()):
                result = ingest.audit_sample(n=1)
        self.assertEqual(result['sampled'],1)
        self.assertEqual(result['income_mismatch'],0)
        with db.get_db() as conn:
            self.assertEqual(conn.execute('SELECT expenses_method FROM filings').fetchone()[0],'c')

    def test_malformed_lobbyist_entries_do_not_block_core_ingest(self):
        good = FIXTURES['self_filer']['lobbying_activities'][0]['lobbyists'][1]
        malformed = [None, 'unexpected', 42, ['unexpected'], {'first_name':'Jane','new':True},
                     {'lobbyist':None}, {'lobbyist':{'id':[]}},
                     {'lobbyist':{'id':42,'first_name':[]}},
                     {'lobbyist':{'id':42},'covered_position':{}},
                     {'lobbyist':{'id':42},'new':'true'}]
        for index, entry in enumerate(malformed):
            with self.subTest(entry=entry):
                raw = copy.deepcopy(FIXTURES['self_filer'])
                raw['filing_uuid'] = f'malformed-{index}'
                raw['lobbying_activities'][0]['lobbyists'] = [entry, good]
                parsed = ingest.parse_api_filing(raw)
                self.assertFalse(parsed['details_complete'])
                self.assertEqual(ingest.load_filings_to_db([parsed]),1)
                with db.get_db() as conn:
                    filing = conn.execute('SELECT * FROM filings WHERE sopr_filing_id=?',
                                          (raw['filing_uuid'],)).fetchone()
                    self.assertEqual(filing['income'],1250000)
                    self.assertIsNone(filing['details_fetched_at'])
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM activities WHERE filing_id=?',
                                                  (filing['id'],)).fetchone()[0],1)
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM filing_lobbyists WHERE filing_id=?',
                                                  (filing['id'],)).fetchone()[0],2)

    def test_malformed_roster_does_not_block_core_ingest(self):
        for index, roster in enumerate(({}, 'unexpected', 1)):
            with self.subTest(roster=roster):
                raw = copy.deepcopy(FIXTURES['self_filer'])
                raw['filing_uuid'] = f'roster-{index}'
                raw['lobbying_activities'][0]['lobbyists'] = roster
                parsed = ingest.parse_api_filing(raw)
                self.assertFalse(parsed['details_complete'])
                self.assertEqual(ingest.load_filings_to_db([parsed]),1)

    def test_expense_detail_oddities_do_not_block_core_ingest(self):
        for index, value in enumerate(({}, [], True, 'unknown', float('inf'), 10**400)):
            with self.subTest(value=value):
                raw = copy.deepcopy(FIXTURES['firm'])
                raw.update(filing_uuid=f'expenses-{index}', expenses=value, expenses_method={})
                parsed = ingest.parse_api_filing(raw)
                self.assertFalse(parsed['details_complete'])
                self.assertIsNone(parsed['expenses'])
                self.assertIsNone(parsed['expenses_method'])
                self.assertEqual(ingest.load_filings_to_db([parsed]),1)
                with db.get_db() as conn:
                    filing = conn.execute('SELECT * FROM filings WHERE sopr_filing_id=?',
                                          (raw['filing_uuid'],)).fetchone()
                    self.assertEqual(filing['income'],40000)
                    self.assertIsNone(filing['details_fetched_at'])
        raw = dict(FIXTURES['self_filer'], expenses={'amount':100})
        parsed = ingest.parse_api_filing(raw)
        self.assertEqual(parsed['income'],0)
        self.assertFalse(parsed['details_complete'])
        self.assertEqual(ingest.load_filings_to_db([parsed]),1)

    def test_detail_write_failure_preserves_core_filing(self):
        parsed = ingest.parse_api_filing(FIXTURES['self_filer'])
        original = ingest.store_filing_details

        def fail_after_write(conn, filing_id, filing):
            original(conn,filing_id,filing)
            raise sqlite3.IntegrityError('unexpected detail constraint')

        with patch.object(ingest,'store_filing_details',side_effect=fail_after_write):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ingest.load_filings_to_db([parsed]),1)
        with db.get_db() as conn:
            filing = conn.execute('SELECT * FROM filings').fetchone()
            self.assertEqual(filing['income'],1250000)
            self.assertIsNone(filing['expenses_method'])
            self.assertIsNone(filing['details_fetched_at'])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM activities').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM filing_lobbyists').fetchone()[0],0)

    def test_missing_expenses_method_is_completed_counted_once_and_not_refetched(self):
        first = self.load()
        second = self.load('firm')
        for raw in (first,second):
            raw.pop('expenses_method')
        uuids = [raw['filing_uuid'] for raw in (first,second)]
        with db.get_db() as conn:
            conn.execute('UPDATE filings SET details_fetched_at=NULL')
            conn.commit()
        stats, get = self.run_backfill([self.response(first),self.response(second)],
                                      uuids=uuids,max_requests=2)
        self.assertEqual(stats['updated'],2)
        self.assertEqual(stats['errors'],0)
        self.assertEqual(stats['missing_expenses_method'],2)
        self.assertEqual(self.backfill_output.count('missing_expenses_method'),1)
        self.assertIn("'missing_expenses_method': 2",self.backfill_output)
        with db.get_db() as conn:
            for row in conn.execute('SELECT expenses_method,details_fetched_at FROM filings'):
                self.assertIsNone(row['expenses_method'])
                self.assertIsNotNone(row['details_fetched_at'])
        stats, get = self.run_backfill([],uuids=uuids)
        self.assertEqual(stats['eligible'],0)
        get.assert_not_called()

    def test_empty_roster_is_complete_and_not_refetched(self):
        raw = self.load('firm', lobbying_activities=[])
        stats, get = self.run_backfill([], uuids=[raw['filing_uuid']])
        self.assertEqual(stats['eligible'],0)
        get.assert_not_called()

    def test_zero_request_cap(self):
        raw = self.load()
        with db.get_db() as conn:
            conn.execute('UPDATE filings SET details_fetched_at=NULL')
            conn.commit()
        stats, get = self.run_backfill([], uuids=[raw['filing_uuid']],max_requests=0)
        self.assertEqual(stats['eligible'],1)
        self.assertEqual(stats['requests'],0)
        get.assert_not_called()

    def test_wall_clock_budget_stops_before_request(self):
        raw = self.load()
        with db.get_db() as conn:
            conn.execute('UPDATE filings SET details_fetched_at=NULL')
            conn.commit()
        with patch.object(ingest.time, 'monotonic', side_effect=[0,0,10,10]):
            stats, get = self.run_backfill([], uuids=[raw['filing_uuid']],max_minutes=0.1)
        self.assertEqual(stats['requests'],0)
        get.assert_not_called()

    def test_refresh_orders_export_backfill_reexport_and_survives_outage(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        refresh_spec = importlib.util.spec_from_file_location('refresh', ROOT / '07_refresh.py')
        refresh = importlib.util.module_from_spec(refresh_spec)
        refresh_spec.loader.exec_module(refresh)
        calls = []
        trends = SimpleNamespace(export_json=Mock(side_effect=lambda: calls.append('export')),
                                 export_clients_json=Mock(side_effect=lambda: calls.append('reexport')))
        details = SimpleNamespace(backfill_details=Mock(side_effect=lambda **kw:
                                  calls.append('backfill') or {'updated':1}))
        with patch.object(refresh, '_load_module', side_effect=[trends,details]):
            refresh.refresh(ingest_latest=False,rules_batch_size=0,verbose=False)
        self.assertEqual(calls,['export','backfill','reexport'])
        details.backfill_details.assert_called_once_with(max_requests=600,max_minutes=8)
        details.backfill_details.side_effect = RuntimeError('API unavailable')
        trends.export_clients_json.reset_mock()
        with patch.object(refresh, '_load_module', side_effect=[trends,details]):
            refresh.refresh(ingest_latest=False,rules_batch_size=0,verbose=False)
        trends.export_clients_json.assert_not_called()

    def test_keyed_auth(self):
        with patch.object(ingest, 'LDA_API_KEY', 'test-token'):
            self.assertEqual(ingest.get_headers(), {'Authorization':'Token test-token'})


if __name__ == '__main__':
    unittest.main()
