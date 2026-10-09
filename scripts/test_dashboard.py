"""Dashboard formatting regressions. Run: python scripts/test_dashboard.py (requires Node.js)."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for dashboard tests')
class DashboardTests(unittest.TestCase):
    def evaluate(self, expression):
        # Run the actual app helpers without starting page initialization.
        script = """
            const fs = require('fs'), vm = require('vm');
            const context = vm.createContext({document: {addEventListener() {}}});
            vm.runInContext(fs.readFileSync('docs/app.js', 'utf8'), context);
            process.stdout.write(JSON.stringify(vm.runInContext(process.argv[1], context)));
        """
        result = subprocess.run(['node', '-e', script, expression], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_normal_baseline_and_share_delta_in_both_frames(self):
        output = self.evaluate("""['quarter', 'qtd'].map(frame => buildSecondaryLine({
            count: 1632, baseline_count: 1266, current_share_pct: 3.6, share_delta_pp: 0.7
        }, frame))""")
        self.assertEqual(output, [
            '1,632 mentions vs 1,266 a year ago · 3.6% of activity (+0.7 pp)',
            '1,632 mentions vs 1,266 at this point last year · 3.6% of activity (+0.7 pp)',
        ])

    def test_new_signals_omit_redundant_delta_across_categories(self):
        output = self.evaluate("""SIGNAL_MODES.flatMap(mode => ['quarter', 'qtd'].map(frame =>
            buildSecondaryLine({mode, count: 696, baseline_count: 0,
                current_share_pct: 1.5, share_delta_pp: 1.5}, frame)))""")
        self.assertEqual(output, [
            '696 mentions · 1.5% of all activity · none a year ago',
            '696 mentions · 1.5% of all activity · none at this point last year',
        ] * 3)

    def test_new_status_uses_mentions_and_zero_zero_is_not_new(self):
        output = self.evaluate("""[
            signalComparison({count: 10, baseline_count: 0, baseline_client_count: 3}, 'quarter').isNew,
            signalComparison({count: 10, baseline_count: 5, baseline_client_count: 0}, 'quarter').isNew,
            signalComparison({count: 0, baseline_count: 0}, 'quarter').isNew
        ]""")
        self.assertEqual(output, [True, False, False])

    def test_decline_zero_share_and_missing_share(self):
        output = self.evaluate("""[
            buildSecondaryLine({count: 0, baseline_count: 100, current_share_pct: 0, share_delta_pp: -1.2}, 'quarter'),
            buildSecondaryLine({count: 1, baseline_count: 0}, 'quarter')
        ]""")
        self.assertEqual(output, [
            '0 mentions vs 100 a year ago · 0.0% of activity (-1.2 pp)',
            '1 mention · none a year ago',
        ])

    def test_normalization_copy_follows_category_and_exported_base_year(self):
        output = self.evaluate("""(() => {
            state.timeseries = {deflators: {base_year: 2030, factors: {}}};
            return ['clients', 'topics', 'legislation', 'entities', 'all'].map(normalizationExplanation);
        })()""")
        dollars = 'Dollars adjusted for inflation (CPI-U, 2030 dollars).'
        mentions = 'Mentions as a share of all tagged activity in each quarter.'
        self.assertEqual(output, [[dollars], [mentions], [mentions], [mentions], [dollars, mentions]])

    def test_covered_position_labels_use_consistent_capitalization(self):
        output = self.evaluate("""[
            'Member of Congress', 'congressional staff', 'executive branch', 'military', 'other',
            'congressional staff, executive branch'
        ].map(coveredPositionClassLabel)""")
        self.assertEqual(output, [
            'Member of Congress', 'Congressional staff', 'Executive branch', 'Military', 'Other',
            'Congressional staff, Executive branch',
        ])


if __name__ == '__main__':
    unittest.main()
