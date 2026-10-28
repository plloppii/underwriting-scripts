"""Check independent task boundaries, JSON output and compatibility of combined facts."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import property_sources as sources
import property_tasks as tasks

LOT = {'bbl': '3010100021', 'address': '1 EXAMPLE STREET', 'borough': 'BK', 'block': '1010', 'lot': '21',
       'latitude': '40.7', 'longitude': '-74', 'lotarea': '1000', 'residfar': '2', 'bldgarea': '0'}
ORIGIN = {'bbl': LOT['bbl'], 'source_url': 'https://example.invalid/pluto'}


class TaskTests(unittest.TestCase):
    def collect(self, task, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return tasks.collect(task, dict(LOT), ORIGIN, **kwargs)

    def test_violations_only_queries_three_violation_sources(self):
        queried = []
        def query(dataset, params):
            queried.append((dataset, params))
            return []
        with patch.object(sources, 'soda', side_effect=query), patch.object(sources, 'flood_zone') as flood, \
                patch.object(sources, 'acris_deeds') as deeds:
            result = self.collect('violations')
        self.assertEqual([ds for ds, _ in queried], [sources.DS[k] for k in ('dobviol', 'ecb', 'hpd')])
        self.assertIn("block='01010'", queried[0][1]['$where'])
        self.assertIn("lot='00021'", queried[0][1]['$where'])
        self.assertIn("lot='0021'", queried[1][1]['$where'])
        self.assertEqual(result['counts'], {'dob': 0, 'ecb': 0, 'hpd': 0})
        flood.assert_not_called()
        deeds.assert_not_called()

    def test_permits_only_queries_both_job_systems(self):
        queried = []
        def query(dataset, params):
            queried.append(dataset)
            return [{'job_type': 'Alteration'}]
        with patch.object(sources, 'soda', side_effect=query):
            result = self.collect('permits')
        self.assertEqual(queried, [sources.DS[k] for k in ('bisjobs', 'dobnow')])
        self.assertEqual(result['counts'], {'bis': 1, 'dob_now': 1})
        self.assertEqual(result['status'], 'complete')

    def test_partial_violation_source_retains_known_records_and_null_unknown_count(self):
        with patch.object(sources, 'dob_violation_rows', side_effect=RuntimeError('Unavailable')), \
                patch.object(sources, 'ecb_violation_rows', return_value=[]), \
                patch.object(sources, 'hpd_violation_rows', return_value=[{'class': 'C'}]):
            result = self.collect('violations')
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['violations']['dob'])
        self.assertIsNone(result['counts']['dob'])
        self.assertEqual(result['counts']['ecb'], 0)
        self.assertEqual(result['counts']['hpd'], 1)

    def test_all_sources_failed_is_different_from_empty_success(self):
        with patch.object(sources, 'soda', side_effect=RuntimeError('Unavailable')):
            result = self.collect('permits')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['counts'], {'bis': None, 'dob_now': None})

    def test_facts_keep_zero_and_missing_far_stays_unknown(self):
        with patch.object(sources, 'near_corner_estimate', return_value=None), patch.object(sources, 'dob_facts') as dob:
            result = self.collect('property_facts')
        self.assertEqual(result['numeric']['bldgarea'], 0)
        self.assertEqual(result['numeric']['unused_sf'], 2000)
        dob.assert_not_called()
        self.assertIsNone(tasks.numeric_facts({'lotarea': '1000'})['max_zfa'])

    def test_sales_keep_zero_deeds_and_only_read_latest_parties(self):
        deeds = [{'document_id': 'old', 'date': '2020-01-01', 'amt': 100},
                 {'document_id': 'latest', 'date': '2021-01-01', 'amt': 0}]
        with patch.object(sources, 'acris_deeds', return_value=deeds), \
                patch.object(sources, 'acris_parties', return_value={'buyer': ['Example LLC'], 'seller': []}) as parties:
            result = self.collect('sales_history')
        self.assertEqual(result['deeds'][0]['amt'], 0)
        parties.assert_called_once_with('latest')
        self.assertEqual(result['parties_document_id'], 'latest')

    def test_sales_retain_deeds_when_party_query_fails(self):
        with patch.object(sources, 'acris_deeds', return_value=[{'document_id': '1', 'date': '2020-01-01', 'amt': 0}]), \
                patch.object(sources, 'acris_parties', side_effect=RuntimeError('Unavailable')):
            result = self.collect('sales_history')
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['parties'])
        self.assertEqual(len(result['deeds']), 1)

    def test_flood_failure_is_unknown_and_never_false_safety(self):
        for effective, preliminary, expected in [(True, True, 'complete'), (False, True, 'partial'), (False, False, 'failed')]:
            with self.subTest(effective=effective, preliminary=preliminary), patch.object(sources, 'flood_zone',
                    return_value={'effective_ok': effective, 'preliminary_ok': preliminary, 'sfha': False, 'psfha': False}):
                result = self.collect('flood')
            self.assertEqual(result['status'], expected)
            if not effective:
                self.assertIsNone(result['flood']['sfha'])

    def test_comps_pass_scope_and_return_candidates_if_signal_source_fails(self):
        candidates = [{'bbl': '3010100022'}]
        with patch.object(sources, 'find_comps', return_value=candidates) as find, \
                patch.object(sources, 'renovation_signal', side_effect=RuntimeError('Unavailable')):
            result = self.collect('comps', radius=1, months=12, min_amt=200000)
        find.assert_called_once_with(LOT, 1, 12, 200000)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['candidate_comps'], candidates)

    def test_combined_building_view_uses_the_same_independent_queries(self):
        with patch.object(sources, 'dob_violation_rows', return_value=[]) as dob, \
                patch.object(sources, 'ecb_violation_rows', return_value=[]) as ecb, \
                patch.object(sources, 'hpd_violation_rows', return_value=[]) as hpd, \
                patch.object(sources, 'bis_job_rows', return_value=[]) as bis, \
                patch.object(sources, 'dobnow_job_rows', return_value=[]) as now, \
                patch.object(sources, 'soda', return_value=[{'bin': '3000001'}]):
            result = sources.dob_facts('3', '1010', '21', LOT['bbl'])
        for query in (dob, ecb, hpd, bis, now):
            query.assert_called_once()
        self.assertEqual(result['dob_viol_open'], 0)
        self.assertEqual(result['active_dobnow'], [])

    def test_cli_writes_exactly_one_json_document_and_matching_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            with patch.object(tasks, 'resolve_origin', return_value=(40.7, -74, ORIGIN)), \
                    patch.object(sources, 'pluto_by_bbl', return_value=dict(LOT)), \
                    patch.object(sources, 'bis_job_rows', return_value=[]), \
                    patch.object(sources, 'dobnow_job_rows', return_value=[]), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
                code = tasks.main('permits', ['--bbl', LOT['bbl'], '--out', str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(), stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())['task'], 'permits')

    def test_invalid_inputs_make_no_network_requests(self):
        for task, args in [('violations', ['--bbl', 'bad']), ('permits', ['An address']),
                           ('comps', ['--bbl', LOT['bbl'], '--radius', 'nan'])]:
            with self.subTest(task=task), patch.object(tasks, 'resolve_origin') as resolve, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    tasks.main(task, args)
                resolve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
