"""Public release DB contents. Run: python scripts/test_release_db.py"""

import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('make_release_db', ROOT / 'scripts/make_release_db.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseDbTests(unittest.TestCase):
    def test_legacy_tables_are_dropped_and_pipeline_tables_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / 'src.db', Path(tmp) / 'out.db'
            conn = sqlite3.connect(src)
            for table in ('filings', 'clients', 'activities', 'activity_extractions_rules',
                          'issues', 'signals', 'activity_extractions'):
                conn.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
                conn.execute(f'INSERT INTO {table} VALUES (1)')
            conn.commit()
            conn.close()
            with patch.object(sys, 'argv', ['make_release_db.py', '--src', str(src), '--out', str(out)]), \
                    redirect_stdout(io.StringIO()):
                release.main()
            conn = sqlite3.connect(out)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
        self.assertEqual(tables, {'filings', 'clients', 'activities', 'activity_extractions_rules'})


if __name__ == '__main__':
    unittest.main()
