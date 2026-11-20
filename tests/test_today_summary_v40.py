"""Homepage totals are isolated, confirmed daily cash flows in the selected month."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import finance
import finance_hub as hub


class TodaySummaryTest(unittest.TestCase):
    def test_daily_summary_omits_other_tracks_unconfirmed_future_and_transfers(self):
        with tempfile.TemporaryDirectory(prefix='today-v40-') as temporary, \
             patch.object(finance, 'DB', Path(temporary)/'finance.sqlite3'), \
             patch.object(hub, 'today', lambda: '2026-10-24'):
            def entry(**values):
                data = dict(date='2026-10-20', name='合成首页统计', cents=1000,
                            type='expense', category='生活', track='daily', confirmed=True)
                data.update(values)
                return finance.save(data)
            entry(cents=12345)
            entry(type='refund', cents=345)
            entry(type='income', cents=50000)
            entry(type='transfer', cents=900000, direction='out')
            entry(type='financing', cents=800000, direction='out')
            entry(track='smart_home', cents=700000)
            entry(confirmed=False, cents=600000)
            entry(date='2026-09-30', cents=500000)
            entry(date='2026-10-25', cents=400000)
            entry(type='excluded', cents=300000, direction='out')
            snapshot = hub.state('2026-10')['summary']
            self.assertEqual(snapshot['daily_net_expense_cents'], 12000)
            self.assertEqual(snapshot['daily_confirmed_count'], 3)
            empty = hub.state('2026-08')['summary']
            self.assertEqual(empty['daily_net_expense_cents'], 0)
            self.assertEqual(empty['daily_confirmed_count'], 0)


if __name__ == '__main__':
    unittest.main()
