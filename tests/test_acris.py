import datetime as dt
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import property_sources as data


class AcrisTests(unittest.TestCase):
    def test_batches_preserve_exact_pairs_order_and_multi_lot_counts(self):
        # API ordering across blocks differs from the caller's per-block order.
        legals = [{'block': b, 'lot': l, 'document_id': doc} for b, l, doc in
                  [('20', '2', 'sale'), ('10', '3', 'other'), ('10', '1', 'sale')]]
        masters = [{'document_id': 'sale', 'document_amt': '0', 'document_date': '2026-01-01'},
                   {'document_id': 'other', 'document_amt': '200000', 'recorded_datetime': '2026-02-01'}]
        queries = []

        def soda(dataset, params):
            queries.append((dataset, params))
            return legals if dataset == data.DS['legals'] else masters

        with patch.object(data, 'soda', side_effect=soda):
            result = data.acris_deeds('3', [('010', '01'), ('20', '2'), ('10', '3')], None)
        where = queries[0][1]['$where']
        self.assertIn("borough='3' AND (", where)
        self.assertIn("(block='10' AND lot in ('1','3'))", where)
        self.assertIn(" OR (block='20' AND lot in ('2'))", where)
        self.assertNotIn('document_amt >=', queries[1][1]['$where'])
        self.assertEqual([(r['block'], r['lot']) for r in result], [('10', '3'), ('10', '1'), ('20', '2')])
        self.assertEqual([r['lots_in_doc'] for r in result], [1, 2, 2])
        self.assertEqual(result[1]['amt'], 0)
        self.assertEqual(result[0]['date'], '2026-02-01')

    def test_large_batch_urls_are_bounded(self):
        pairs = [(str(block), str(lot)) for block in range(1000, 1020) for lot in range(1, 101)]
        with patch.object(data, 'soda', return_value=[]) as soda:
            self.assertEqual(data.acris_deeds('3', pairs, None), [])
        self.assertGreater(soda.call_count, 1)
        self.assertLess(soda.call_count, 40)  # Previously two requests per block.
        for call in soda.call_args_list:
            self.assertEqual(call.args[0], data.DS['legals'])
            self.assertLessEqual(len(urlencode({'$where': call.args[1]['$where']})), 6000)

    def test_recent_dense_search_reads_disjoint_windows_and_keeps_future_dates(self):
        since = dt.date.today() - dt.timedelta(days=540)
        legals = [{'block': '10', 'lot': '1', 'document_id': str(i)} for i in range(3001)]
        source = [{'document_id': str(i), 'document_date': date.isoformat(), 'document_amt': '100000'}
                  for i, date in enumerate([since, since + dt.timedelta(days=120),
                                            dt.date.today() + dt.timedelta(days=365)])]
        source.append({'document_id': 'outside-requested-lots', 'document_date': since.isoformat(), 'document_amt': '100000'})
        windows = []

        def soda(dataset, params):
            if dataset == data.DS['legals']:
                return legals
            where = params['$where']
            self.assertIn("doc_type='DEED'", where)
            self.assertIn('document_amt >= 100000', where)
            self.assertNotIn('document_id in', where)
            lo = re.search(r"document_date >= '(\d{4}-\d{2}-\d{2})T", where)[1]
            hi = re.search(r"document_date < '(\d{4}-\d{2}-\d{2})T", where)
            hi = hi[1] if hi else None
            windows.append((lo, hi))
            return [r for r in source if r['document_date'] >= lo and (not hi or r['document_date'] < hi)]

        with patch.object(data, 'soda', side_effect=soda):
            result = data.acris_deeds('3', [('10', '1')], since.isoformat(), 100000)
        self.assertEqual([r['document_id'] for r in result], ['0', '1', '2'])
        windows.sort()
        self.assertEqual(windows[0][0], since.isoformat())
        self.assertIsNone(windows[-1][1])
        self.assertEqual([hi for _, hi in windows[:-1]], [lo for lo, _ in windows[1:]])
        self.assertEqual(len(windows), 5)

    def test_long_history_stays_scoped_to_ids(self):
        legals = [{'block': '10', 'lot': '1', 'document_id': str(i)} for i in range(3001)]
        queries = []

        def soda(dataset, params):
            if dataset == data.DS['legals']:
                return legals
            queries.append(params['$where'])
            return []

        with patch.object(data, 'soda', side_effect=soda):
            result = data.acris_deeds('3', [('10', '1')], '1900-01-01', 100000)
        self.assertEqual(result, [])
        self.assertEqual(len(queries), 21)
        self.assertTrue(all("document_id in (" in q and "document_date >= '1900-01-01T" in q for q in queries))

    def test_paginated_legals_are_not_truncated_by_batching(self):
        row = {'block': '10', 'lot': '1', 'document_id': 'sale'}
        queries = []

        def get(url, params, timeout):
            queries.append((url, dict(params)))
            if data.DS['legals'] in url:
                rows = [row] * 5000 if not params.get('$offset') else [{**row, 'document_id': 'last'}]
            else:
                rows = [{'document_id': 'last', 'document_amt': '500000'}]
            response = Mock(status_code=200)
            response.json.return_value = rows
            return response

        with patch.object(data.SESSION, 'get', side_effect=get):
            result = data.acris_deeds('3', [('10', '1')], None)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['document_id'], 'last')
        self.assertEqual(queries[1][1]['$offset'], 5000)

    def test_failed_window_propagates_instead_of_returning_partial_sales(self):
        legals = [{'block': '10', 'lot': '1', 'document_id': str(i)} for i in range(3001)]

        def soda(dataset, params):
            if dataset == data.DS['legals']:
                return legals
            raise RuntimeError('Unavailable')

        since = (dt.date.today() - dt.timedelta(days=540)).isoformat()
        with patch.object(data, 'soda', side_effect=soda), self.assertRaises(RuntimeError):
            data.acris_deeds('3', [('10', '1')], since, 100000)

    def test_empty_lot_list_skips_requests(self):
        with patch.object(data, 'soda') as soda:
            self.assertEqual(data.acris_deeds('3', [], None), [])
        soda.assert_not_called()


if __name__ == '__main__':
    unittest.main()
