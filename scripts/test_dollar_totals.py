"""Dollar-total conventions shared by every export: self-filer
de-duplication, verified-erroneous filer exclusion, ended quarters only,
tag-amount ranking, and decliners to zero. Run: python scripts/test_dollar_totals.py"""

import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import db

spec = importlib.util.spec_from_file_location('trends_totals', ROOT / '08_trends.py')
trends = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = trends
spec.loader.exec_module(trends)

LOC = 'State of Loc Nation Global Public Benefit Corporation'


class DuplicateTests(unittest.TestCase):
    def test_outside_filings_drop_only_where_the_client_self_filed_that_period(self):
        rows = [
            (1, 'ANTHROPIC', (2026, 2), 1),     # own report
            (2, 'ANTHROPIC', (2026, 2), 0),     # outside firm, same quarter
            (3, 'ANTHROPIC', (2026, 2), None),  # unknown flag counts as outside
            (4, 'ANTHROPIC', (2026, 1), 0),     # no own report that quarter
            (5, 'OTHER', (2026, 2), 0),
            (6, '', (2026, 2), 0),
            (7, 'OTHER', (2026, 2), 1),
            (8, 'OTHER', (2026, 2), 1),         # several own reports all count
        ]
        self.assertEqual(trends.self_filed_duplicate_ids(iter(rows)), {2, 3, 5})


class ExportTotalsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.object(db, 'DB_PATH', Path(tmp.name) / 'filings.db'))
        trends.load_reference.cache_clear()
        trends._LEARNED_ALIASES = None
        with db.get_db() as conn:
            conn.executescript(db.SCHEMA)
            conn.execute('''CREATE TABLE activity_extractions_rules (activity_id INTEGER, coarse_topic TEXT,
                            topics TEXT, entities TEXT, legislation TEXT)''')
            conn.executemany('INSERT INTO clients (id, name) VALUES (?, ?)', [
                (1, 'BIG ORG, INC.'), (2, 'Big Org'), (3, LOC), (4, 'Plain Client')])
            conn.executemany('INSERT INTO registrants (id, name) VALUES (?, ?)', [
                (1, 'Big Org'), (2, 'Firm LLP'), (3, LOC), (4, 'Other Firm')])
            # id, registrant, client, year, quarter, income, filing_date, type, is_current, self
            filings = [
                (1, 1, 1, 2026, 2, 1000000, '2026-07-20', 'Q2', 1, 1),
                (2, 2, 2, 2026, 2, 200000, '2026-07-18', 'Q2', 1, 0),   # inside Big Org's report
                (3, 3, 3, 2026, 2, 20000000, '2026-07-19', 'Q2', 1, 1),  # excluded filer
                (4, 4, 4, 2026, 2, 300000, '2026-07-19', 'Q2', 1, 0),
                (5, 1, 1, 2025, 2, 500000, '2025-07-20', 'Q2', 1, 1),
                (6, 4, 4, 2025, 2, 100000, '2025-07-20', 'Q2', 1, 0),
                (7, 4, 4, 2026, 3, 50000, '2026-10-20', 'Q3', 1, 0),
                (8, 4, 4, 2026, 4, 10000, '2026-10-21', '4T', 1, 0),     # period not over
            ]
            conn.executemany('''INSERT INTO filings (id, registrant_id, client_id, year, quarter, income,
                                filing_date, filing_type, is_current, is_self_filer)
                                VALUES (?,?,?,?,?,?,?,?,?,?)''', filings)
            # Big Org's own report covers ten issues, one of them "alpha";
            # Plain Client's whole $300K report is about alpha.
            activities = [(1, 'alpha')] + [(1, 'beta')] * 9 + [(2, 'alpha'), (3, 'alpha'), (4, 'alpha'),
                                                              (5, 'alpha'), (6, 'alpha')]
            for aid, (fid, topic) in enumerate(activities, start=1):
                conn.execute('INSERT INTO activities (id, filing_id, description) VALUES (?, ?, ?)',
                             (aid, fid, topic))
                conn.execute("INSERT INTO activity_extractions_rules VALUES (?, 'x', ?, '[]', '[]')",
                             (aid, json.dumps([topic])))
            conn.commit()

    def test_stats_and_timeseries_agree_on_deduplicated_ended_quarters(self):
        stats = trends.get_stats()
        series = trends.get_time_series(200)
        lcq = stats['latest_complete_quarter']
        self.assertEqual(lcq['label'], 'Q2 2026')
        # $1M own report + $300K; the firm's $200K is inside Big Org's
        # report, and the $20M excluded filer counts for nothing.
        self.assertEqual((lcq['income'], lcq['filings']), (1300000, 3))
        self.assertEqual((lcq['yoy_income'], lcq['yoy_filings']), (600000, 2))
        self.assertEqual(stats['current_partial_quarter']['filings'], 1)
        self.assertEqual([(q['year'], q['quarter'], q['income'], q['filings']) for q in stats['quarters']],
                         [(2026, 3, 50000, 1), (2026, 2, 1300000, 3), (2025, 2, 600000, 2)])
        self.assertEqual([(q['year'], q['quarter'], q['income'], q['filings']) for q in series['quarters']],
                         [(2025, 2, 600000, 2), (2026, 2, 1300000, 3), (2026, 3, 50000, 1)])
        # Mentions exclude the erroneous filer too, and no Q4 2026 point.
        self.assertEqual([q['mentions'] for q in series['quarters']], [2, 12, 0])
        self.assertEqual(series['context']['end_label'], '2026 Q3')

    def test_tag_income_is_deduplicated_and_clients_rank_by_tag_amount(self):
        counts = trends.get_extraction_counts(2026, 2)
        self.assertEqual(counts['total_rows'], 12)
        self.assertEqual(counts['topics']['Alpha'], 3)
        self.assertEqual(counts['topic_income']['Alpha'], 1300000)
        # Big Org's whole report is $1M but only a tenth of it is on alpha;
        # Plain Client's $300K is all alpha.
        self.assertEqual(counts['topic_clients']['Alpha'], ['Plain Client', 'Big Org'])
        self.assertEqual(counts['topic_clients']['Beta'], ['Big Org, Inc.'])
        firm_example = next(e for e in counts['topic_examples']['Alpha'] if e['id'] == 2)
        self.assertEqual(firm_example['income'], 200000)
        self.assertTrue(firm_example['included_in_self_report'])
        with db.get_db() as conn:
            self.assertEqual(trends._filing_count(conn, 2026, 2), 3)

    def test_movers_and_headline_use_the_same_totals(self):
        specs = {
            'quarter': {'current': (2026, 2, None), 'baseline': (2025, 2, None), 'complete': True},
            'qtd': {'current': (2026, 3, '2026-10-21'), 'baseline': (2025, 3, '2025-10-21'),
                    'complete': False, 'through': '2026-10-21'},
        }
        with patch.object(trends, '_frame_specs', return_value=specs):
            movers = trends.compute_client_movers()
        totals = movers['frames']['quarter']['quarter_totals']
        lcq = trends.get_stats()['latest_complete_quarter']
        self.assertEqual((totals['current_income'], totals['baseline_income']), (lcq['income'], lcq['yoy_income']))
        big = next(m for g in ('risers', 'fallers', 'new_entrants', 'majors')
                   for m in movers['frames']['quarter'][g] if m['key'] == 'BIG ORG')
        self.assertEqual((big['current'], big['baseline'], big['filings_current']), (1000000, 500000, 2))


class ChangeTests(unittest.TestCase):
    def test_tags_that_fell_to_zero_rank_as_decliners(self):
        current = {'topics': Counter({'steady': 100}), 'topic_client_count': {'steady': 9}, 'total_rows': 1000}
        baseline = {'topics': Counter({'steady': 100, 'gone': 40, 'tiny': 1}),
                    'topic_client_count': {'gone': 12}, 'total_rows': 1000}
        items, meta = trends.calc_change(current, baseline, 'topics', min_count=5)
        gone = next(i for i in items if i['name'] == 'gone')
        self.assertEqual((gone['count'], gone['baseline_count'], gone['client_count'],
                          gone['baseline_client_count']), (0, 40, 0, 12))
        self.assertEqual(gone['share_delta_pp'], -4.0)
        self.assertEqual(gone['confidence'], 'medium')
        self.assertEqual(items[0]['name'], 'gone')
        self.assertNotIn('tiny', [i['name'] for i in items])
        self.assertEqual((meta['dropped_min_count'], meta['unique_baseline_only']), (1, 2))
        alerts = trends.generate_alerts({'topics': {'quarter': items}, 'frames': {}})
        self.assertEqual([(a['name'], a['current_count']) for a in alerts], [('gone', 0)])


if __name__ == '__main__':
    unittest.main()
