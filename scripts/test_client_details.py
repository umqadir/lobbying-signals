"""Organization detail export regressions. Run: python scripts/test_client_details.py"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import db

spec = importlib.util.spec_from_file_location('trends_details', ROOT / '08_trends.py')
trends = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = trends
spec.loader.exec_module(trends)


def filing(fid=1, **changes):
    return {
        'filing_id': fid, 'filing_uuid': f'uuid-{fid}', 'filing_date': '2026-07-20',
        'registrant_id': fid, 'registrant_name': 'Example Firm LLP', 'income': 40000,
        'is_self_filer': 0, 'expenses_method': None, 'details_fetched_at': None,
        'filing_type': '2A', **changes,
    }


def person(name='Jane Doe', **changes):
    return {'name': name, 'lda_lobbyist_id': None, 'covered_position': None, **changes}


class SummaryTests(unittest.TestCase):
    def test_split_retains_legacy_amounts_and_unknowns(self):
        q = trends.summarize_client_quarter([
            filing(1, is_self_filer=1, income=1000000), filing(2),
            filing(3, is_self_filer=None, income=10000),
        ], [], [])
        self.assertEqual([q['split'][k] for k in ('self', 'outside', 'unknown')], [1000000, 40000, 10000])
        self.assertEqual(q['firm_count'], 1)
        self.assertEqual(q['self_inferred'], 1)
        self.assertIsNone(q['lobbyist_count'])
        self.assertIsNone(q['covered_count'])
        self.assertIsNone(q['position_classes'])
        self.assertEqual(q['methods_missing'], 1)

    def test_unknown_amounts_are_not_zero_and_empty_period_is_known(self):
        q = trends.summarize_client_quarter([filing(income=None)], [], [])
        self.assertIsNone(q['split']['outside'])
        self.assertIsNone(q['firms'][0]['income'])
        self.assertIsNone(q['filings'][0]['amount'])
        empty = trends.summarize_client_quarter([], [], [])
        self.assertEqual(empty['lobbyist_count'], 0)
        self.assertEqual(empty['split']['self'], 0)
        fetched = trends.summarize_client_quarter([filing(details_fetched_at='2026-10-01')], [], [])
        self.assertEqual(fetched['lobbyist_count'], 0)

    def test_method_change_and_firm_arrivals(self):
        cur = trends.summarize_client_quarter([
            filing(1, is_self_filer=1, expenses_method=' c '),
            filing(2, registrant_name='New Firm'), filing(3, registrant_name='Shared Firm Inc.'),
        ], [], [])
        base = trends.summarize_client_quarter([
            filing(4, is_self_filer=1, expenses_method='a'),
            filing(5, registrant_name='Dropped Firm'), filing(6, registrant_name='Shared Firm'),
        ], [], [])
        comparison = trends.compare_client_quarters(cur, base)
        self.assertTrue(comparison['method_changed'])
        self.assertEqual({f['name']: f['status'] for f in cur['firms']}, {'New Firm': 'new', 'Shared Firm Inc.': None})
        self.assertEqual(base['firms'][0]['status'], 'dropped')
        for methods in ([], ['C']):
            base['methods'] = methods
            self.assertFalse(trends.compare_client_quarters(cur, base)['method_changed'])

    def test_classifier_staff_precedence_and_all_classes(self):
        examples = [
            ('Legislative Director, Sen. Example', 'congressional staff'),
            ('Chief of Staff, Senator Example', 'congressional staff'),
            ('Legislative Assistant to Rep. Example', 'congressional staff'),
            ('Legislative Correspondent, Representative Example', 'congressional staff'),
            ('LD, Congressman Example', 'congressional staff'),
            ('LA, Congresswoman Example', 'congressional staff'),
            ('Counsel to U.S. Senator Example', 'congressional staff'),
            ('Staff Director, Committee on Finance', 'congressional staff'),
            ('Professional Staff Member, Senate Armed Services Committee', 'congressional staff'),
            ('Press Secretary, Office of Rep. Example', 'congressional staff'),
            ('Scheduler, Sen. Example', 'congressional staff'),
            ('Caseworker, Rep. Example', 'congressional staff'),
            ('Fellow, House Appropriations Subcommittee', 'congressional staff'),
            ('Counsel, Office of the Majority Leader', 'congressional staff'),
            ('Office of the Speaker', 'congressional staff'),
            ('Office of U.S. Representative Example', 'congressional staff'),
            ('Chief of Staff to Member of Congress', 'congressional staff'),
            ('Member of Congress', 'Member of Congress'),
            ('U.S. Representative', 'Member of Congress'),
            ('Former U.S. Senator', 'Member of Congress'),
            ('Member, U.S. House', 'Member of Congress'),
            ('Member of the U.S. Senate', 'Member of Congress'),
            ('Senator Example', 'Member of Congress'),
            ('White House policy adviser', 'executive branch'),
            ('OMB director', 'executive branch'),
            ('EOP staff', 'executive branch'),
            ('Deputy Assistant Secretary', 'executive branch'),
            ('Secretary, Dept. of Commerce', 'executive branch'),
            ('Secretary of State', 'executive branch'),
            ('Assistant Secretary of the Navy', 'executive branch'),
            ('Administrator, EPA', 'executive branch'),
            ('Commissioner, Federal Communications Commission', 'executive branch'),
            ('Schedule C appointee', 'executive branch'),
            ('Staff, DHS', 'executive branch'),
            ('FDA Advisory Committee staff', 'executive branch'),
            ('General Services Administration', 'executive branch'),
            ('Special Assistant to the President', 'executive branch'),
            ('General Counsel, Department of Energy', 'executive branch'),
            ('Attorney General, Department of Justice', 'executive branch'),
            ('Deputy U.S. Trade Representative', 'executive branch'),
            ('U.S. Representative to the United Nations', 'executive branch'),
            ('Colonel, US Army', 'military'),
            ('Lt., Air Force', 'military'),
            ('Captain USN', 'military'),
            ('General', 'military'),
            ('Admiral, Coast Guard', 'military'),
            ('Marine Corps / USMC', 'military'),
            ('Space Force officer', 'military'),
            ('City council member', 'other'),
            ('General Counsel, Acme Inc.', 'other'),
            ('Lt. Governor', 'other'),
            ('State Senator', 'other'),
        ]
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(trends.classify_covered_position(text), expected)

    def test_lobbyist_identity_position_dedup_and_partial_coverage(self):
        people = [
            person(lda_lobbyist_id=1, covered_position='Chief of Staff, House Committee'),
            person('JANE DOE', covered_position='chief of staff,  house committee'),
            person(lda_lobbyist_id=1, covered_position='White House adviser'),
            person('Other Person', lda_lobbyist_id=2),
            person('No position', covered_position='N/A'),
        ]
        q = trends.summarize_client_quarter([filing(details_fetched_at='now'), filing(2)], people, [])
        self.assertEqual(q['lobbyist_count'], 3)
        self.assertEqual(q['covered_count'], 1)
        self.assertEqual(q['details_fetched'], 1)
        self.assertEqual(len(q['positions']), 1)
        self.assertEqual(q['positions'][0]['text'], 'Chief of Staff, House Committee\n\nWhite House adviser')
        self.assertEqual(q['position_classes']['congressional staff'], 1)
        self.assertEqual(q['position_classes']['executive branch'], 1)

    def test_cross_registrant_ids_merge_transitively_and_counts_use_people(self):
        # Same disclosure bridges IDs 1/2; ID 2's second position bridges
        # ID 3 too, regardless of row order or repeated activity/filing rows.
        rows = [
            person('Alex Poe', lda_lobbyist_id=1, covered_position='Deputy Assistant Secretary'),
            person(' ALEX  POE ', lda_lobbyist_id=2, covered_position='deputy assistant secretary.'),
            person('Alex Poe', lda_lobbyist_id=2, covered_position='Legislative Director, Sen. Example'),
            person('ALEX POE', lda_lobbyist_id=3, covered_position='legislative director, SEN Example'),
            person('Alex Poe', covered_position='Deputy Assistant Secretary'),
            person('Alex Poe', covered_position='White House adviser'),
            person('John Roe', lda_lobbyist_id=4),
            person('JOHN ROE', lda_lobbyist_id=5, covered_position='N/A'),
            person('Jane Doe', lda_lobbyist_id=6, covered_position='U.S. Senator'),
        ]
        for people in (rows * 5, list(reversed(rows)) * 5):
            q = trends.summarize_client_quarter([filing(details_fetched_at='now')], people, [])
            self.assertEqual(q['lobbyist_count'], 3)
            self.assertEqual(q['covered_count'], 2)
            self.assertEqual(len(q['positions']), 2)
            self.assertEqual(q['position_classes'], {
                'Member of Congress': 1, 'congressional staff': 1,
                'executive branch': 1, 'military': 0, 'other': 0,
            })
            alex = next(p for p in q['positions'] if 'poe' in p['name'].lower())
            self.assertEqual(len(alex['text'].split('\n\n')), 3)
            self.assertIn('congressional staff', alex['class'])
            self.assertIn('executive branch', alex['class'])

    def test_same_id_requires_matching_normalized_names(self):
        people = [
            person('Jane Doe', lda_lobbyist_id=1, covered_position='White House adviser'),
            person('Jane Q. Doe', lda_lobbyist_id=1, covered_position='OMB director'),
            person(' JANE  DOE ', lda_lobbyist_id=1, covered_position='White House adviser'),
            person('JANE DOE', lda_lobbyist_id=2, covered_position='Colonel, Army'),
        ]
        q = trends.summarize_client_quarter([filing(details_fetched_at='now')], people, [])
        self.assertEqual(q['lobbyist_count'], 3)
        self.assertEqual(q['covered_count'], 3)
        self.assertEqual(len(q['positions']), 3)
        self.assertEqual(q['position_classes']['executive branch'], 2)
        self.assertEqual(q['position_classes']['military'], 1)
        self.assertTrue(all('\n\n' not in p['text'] for p in q['positions']))

    def test_shared_id_never_merges_different_people_through_disclosures(self):
        people = [
            person('Jane Doe', lda_lobbyist_id=1, covered_position='White House adviser'),
            person('John Roe', lda_lobbyist_id=1, covered_position='White House adviser'),
            person('JANE DOE', lda_lobbyist_id=2, covered_position='white house adviser.'),
            person('Jane Doe', lda_lobbyist_id=2, covered_position='Colonel, Army'),
            person('JOHN ROE', covered_position='Legislative Director, Sen. Example'),
        ]
        for rows in (people * 3, list(reversed(people)) * 3):
            q = trends.summarize_client_quarter([filing(details_fetched_at='now')], rows, [])
            self.assertEqual(q['lobbyist_count'], 2)
            self.assertEqual(q['covered_count'], 2)
            self.assertEqual(q['position_classes']['executive branch'], 2)
            self.assertEqual(q['position_classes']['military'], 1)
            self.assertEqual(q['position_classes']['congressional staff'], 1)
            positions = {trends._lobbyist_identity_key(p['name']): p['text'].lower()
                         for p in q['positions']}
            self.assertIn('colonel', positions['jane doe'])
            self.assertNotIn('legislative', positions['jane doe'])
            self.assertIn('legislative', positions['john roe'])
            self.assertNotIn('colonel', positions['john roe'])

    def test_filing_type_survives_summary_without_fetched_detail(self):
        q = trends.summarize_client_quarter([filing(filing_type='2A'), filing(2, filing_type=None)], [], [])
        self.assertEqual([f['type'] for f in q['filings']], ['2A', None])

    def test_covered_position_placeholders_have_no_count_list_or_class(self):
        placeholders = (None, 'N/A', 'NA', 'None', 'n/a', '-', '', ' \t\n ', ' nA ', 'not applicable')
        for placeholder in placeholders:
            with self.subTest(position=placeholder):
                self.assertIsNone(trends.classify_covered_position(placeholder))
                self.assertIsNone(db.normalize_covered_position(placeholder))
        people = [person(f'Lobbyist {i}', covered_position=text)
                  for i, text in enumerate(placeholders)]
        q = trends.summarize_client_quarter([filing(details_fetched_at='now')], people, [])
        self.assertEqual(q['lobbyist_count'], len(placeholders))
        self.assertEqual(q['covered_count'], 0)
        self.assertEqual(q['positions'], [])
        self.assertTrue(all(count == 0 for count in q['position_classes'].values()))
        # A real, unrecognized disclosure belongs in "other"; placeholders
        # must not inflate its count or hide the actual raw text.
        raw = 'Former city council member;\nserved 2010–2014.'
        people.append(person('Real Position', covered_position=raw))
        q = trends.summarize_client_quarter([filing(details_fetched_at='now')], people, [])
        self.assertEqual(q['covered_count'], 1)
        self.assertEqual(q['position_classes']['other'], 1)
        self.assertEqual([p['text'] for p in q['positions']], [raw])

    def test_dedup_before_truncation_and_list_caps(self):
        text = 'Budget investment. ' * 150
        activities = [{'issue_code': 'BUD', 'description': text},
                      {'issue_code': 'BUD', 'description': text.upper()},
                      {'issue_code': 'TRD', 'description': text}]
        activities += [{'issue_code': 'BUD', 'description': f'Distinct issue {i}'} for i in range(35)]
        people = [person(str(i), covered_position='Former Senator ' + 'x' * 1300) for i in range(35)]
        q = trends.summarize_client_quarter([filing(i, registrant_name=f'Firm {i}') for i in range(35)], people, activities)
        self.assertEqual(len(q['issues']), 30)
        self.assertEqual(q['issues_more'], 7)
        self.assertTrue(q['issues'][0]['truncated'])
        self.assertEqual(len(q['issues'][0]['text']), trends.CLIENT_ISSUE_TEXT_LIMIT)
        self.assertEqual(len(q['positions']), 30)
        self.assertEqual(q['positions_more'], 5)
        self.assertEqual(q['covered_count'], 35)
        self.assertEqual(len(q['filings']), 30)
        self.assertEqual(q['filings_more'], 5)
        self.assertEqual(len(q['positions'][0]['text']), trends.CLIENT_POSITION_TEXT_LIMIT)
        empty = trends.summarize_client_quarter([], [], [])
        trends.compare_client_quarters(q, empty)
        self.assertEqual(len(q['firms']), 30)
        self.assertEqual(q['firms_more'], 5)
        self.assertTrue(all(f['status'] == 'new' for f in q['firms']))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.db_patch = patch.object(db, 'DB_PATH', self.path / 'filings.db')
        self.db_patch.start()
        self.spec_patch = patch.object(trends, '_frame_specs', return_value={
            'quarter': {'current': (2026, 2, None), 'baseline': (2025, 2, None), 'complete': True},
            'qtd': {'current': (2026, 3, '2026-09-01'), 'baseline': (2025, 3, '2025-09-01'),
                    'complete': False, 'through': '2026-09-01'},
        })
        self.spec_patch.start()
        with db.get_db() as conn:
            conn.executescript(db.SCHEMA)
            conn.execute('CREATE TABLE activity_extractions_rules (activity_id INTEGER, topics TEXT)')
            conn.executemany('INSERT INTO clients (id,name) VALUES (?,?)', [(1, 'Acme Inc.'), (2, 'New Entrant')])
            conn.executemany('INSERT INTO registrants (id,name) VALUES (?,?)', [(1, 'Acme'), (2, 'Outside Firm')])
            rows = [
                (1, 1, 1, 2026, 2, 1000000, '2026-07-20', '2A', 1, 1, 'C', 'now'),
                (2, 1, 1, 2025, 2, 500000, '2025-07-20', 'Q2', 1, 1, 'A', 'now'),
                (3, 2, 1, 2026, 2, 100000, '2026-07-20', 'Q2', 1, 0, None, None),
                (4, 2, 2, 2026, 2, 300000, '2026-07-20', 'Q2', 1, 0, None, None),
                # Original won at the QTD cutoff; later amendment is current today.
                (5, 1, 1, 2026, 3, 800000, '2026-08-20', 'Q3', 0, 1, 'A', 'now'),
                (6, 1, 1, 2026, 3, 1200000, '2026-09-20', '3A', 1, 1, 'C', 'now'),
                (7, 1, 1, 2025, 3, 400000, '2025-08-20', 'Q3', 0, 1, 'A', None),
                (8, 1, 1, 2025, 3, 600000, '2025-09-20', '3A', 1, 1, 'C', 'now'),
            ]
            conn.executemany('''INSERT INTO filings (id,registrant_id,client_id,year,quarter,income,
                filing_date,filing_type,is_current,is_self_filer,expenses_method,details_fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
            conn.execute("UPDATE filings SET sopr_filing_id = 'uuid-' || id")
            conn.execute("INSERT INTO activities (id,filing_id,issue_code,description) VALUES (1,1,'BUD','Infrastructure investment.')")
            conn.execute("INSERT INTO filing_lobbyists (filing_id,activity_index,lobbyist_index,name,covered_position) VALUES (1,0,0,'Jane Doe','White House adviser')")
            conn.commit()

    def tearDown(self):
        self.spec_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_both_frames_match_totals_and_preserve_as_of_amendments(self):
        movers = trends.compute_client_movers()
        details = trends.compute_client_details(movers)
        for fk, frame in movers['frames'].items():
            for group in ('risers', 'fallers', 'new_entrants', 'majors'):
                for m in frame[group]:
                    d = details['frames'][fk][m['key']]
                    for leg in ('current', 'baseline'):
                        self.assertEqual(sum(d[leg]['split'][k] for k in ('self', 'outside', 'unknown')), m[leg])
                        self.assertEqual(d[leg]['filing_count'], m[f'filings_{leg}'])
        full = details['frames']['quarter']['ACME']
        self.assertTrue(full['method_changed'])
        acme = next(m for m in movers['frames']['quarter']['risers'] if m['key'] == 'ACME')
        self.assertEqual(acme['examples'][0]['type'], '2A')
        self.assertEqual(full['current']['filings'][0]['type'], '2A')
        self.assertEqual(full['current']['covered_count'], 1)
        qtd = details['frames']['qtd']['ACME']
        self.assertFalse(qtd['method_changed'])
        self.assertEqual(qtd['current']['filings'][0]['uuid'], 'uuid-5')
        self.assertEqual(qtd['baseline']['filings'][0]['uuid'], 'uuid-7')
        entrant = next(iter(movers['frames']['quarter']['new_entrants']))
        self.assertEqual(details['frames']['quarter'][entrant['key']]['baseline']['filing_count'], 0)

    def test_post_backfill_export_regenerates_both_files(self):
        trends.export_clients_json(str(self.path / 'exports'))
        def read(name):
            return json.loads((self.path / 'exports' / name).read_text())
        before = read('client_details.json')
        self.assertTrue(before['frames']['quarter']['ACME']['method_changed'])
        with db.get_db() as conn:
            conn.execute("UPDATE filings SET expenses_method='A' WHERE id=1")
            conn.commit()
        trends.export_clients_json(str(self.path / 'exports'))
        details, clients = read('client_details.json'), read('clients.json')
        self.assertFalse(details['frames']['quarter']['ACME']['method_changed'])
        self.assertEqual(details['generated_at'], clients['generated_at'])
        self.assertFalse(clients['frames']['quarter']['risers'][0]['method_changed'])


if __name__ == '__main__':
    unittest.main()
