import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import screen
import place_sources as places


class PlaceContractTests(unittest.TestCase):
    def test_restaurants_dedupe_by_identity_and_preserve_newest_evidence(self):
        row = {'camis': '1', 'dba': 'Example Cafe', 'latitude': '40.7', 'longitude': '-74', 'inspection_date': '2026-01-01'}
        older = {**row, 'inspection_date': '2025-01-01'}
        with patch.object(places, 'read_rows', return_value=([row, older], '2026-01-01')):
            result = places.collect('restaurants', 40.7, -74, 0.5)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['inspection_date'], '2026-01-01')
        self.assertIn('camis=1', result['records'][0]['source_url'])

    def test_station_radius_filter_and_distinct_ids(self):
        rows = [{'gtfs_stop_id': key, 'stop_name': 'Example Station', 'gtfs_latitude': lat, 'gtfs_longitude': '-74'}
                for key, lat in [('A', '40.7'), ('B', '40.7001'), ('far', '40.72')]]
        with patch.object(places, 'read_rows', return_value=(rows, '2026-01-01')):
            result = places.collect('train_stations', 40.7, -74, 0.5)
        self.assertEqual([r['source_id'] for r in result['records']], ['mta:A', 'mta:B'])

    def test_failed_source_and_duplicate_identity_do_not_look_complete(self):
        row = {'gtfs_stop_id': 'A', 'stop_name': 'Example Station', 'gtfs_latitude': '40.7', 'gtfs_longitude': '-74'}
        with patch.object(places, 'read_rows', return_value=([row, row], '2026-01-01')):
            result = places.collect('train_stations', 40.7, -74, 0.5)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['records'], [])
        with patch.object(places, 'read_rows', side_effect=places.SourceError('Unavailable')):
            result = places.collect('parks', 40.7, -74, 0.5)
        self.assertEqual(result['status'], 'failed')

    def test_place_pagination_fails_on_truncation(self):
        responses = [{'rowsUpdatedAt': 1}, [{'n': '2'}], [{'camis': '1'}], []]
        with patch.object(places, 'fetch', side_effect=responses), self.assertRaises(places.SourceError):
            places.read_rows(Mock(), places.SOURCES['restaurants'], '1=1', page_size=1)


class PropertyContractTests(unittest.TestCase):
    def test_failed_stage_retains_other_facts_and_unknown_numeric_values(self):
        args = argparse.Namespace(address=None, borough=None, bbl='3010100021', radius=0.5, months=18, min_amt=100000, no_comps=True)
        lot = {'address': '1 EXAMPLE STREET', 'bbl': '3010100021', 'borough': 'BK', 'block': '1010', 'lot': '21',
               'lotarea': '1000', 'bldgarea': '0'}
        with patch.object(screen, 'resolve_origin', return_value=(40.7, -74, {'bbl': '3010100021', 'source_url': 'https://example.invalid'})), \
             patch.object(screen.data, 'pluto_by_bbl', return_value=lot), \
             patch.object(screen.data, 'near_corner_estimate', return_value=None), \
             patch.object(screen.data, 'dob_facts', side_effect=RuntimeError('Unavailable')), \
             patch.object(screen.data, 'flood_zone', return_value={'effective_ok': True, 'preliminary_ok': True}), \
             patch.object(screen.data, 'acris_deeds', return_value=[]), contextlib.redirect_stderr(io.StringIO()):
            result = screen.collect(args)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['numeric']['bldgarea'], 0)
        self.assertIsNone(result['numeric']['max_zfa'])
        self.assertIsNone(result['dob'])
        self.assertNotIn('verdict', result)
        self.assertNotIn('body_md', result)

    def test_public_source_reads_beyond_the_default_page_limit(self):
        first = [{"id": str(n)} for n in range(5000)]
        pages = [first, [{"id": "last"}]]
        queries = []
        def get(url, params, timeout):
            queries.append(dict(params))
            response = Mock()
            response.json.return_value = pages.pop(0)
            return response
        with patch.object(screen.data.SESSION, 'get', side_effect=get):
            rows = screen.data.soda('example', {})
        self.assertEqual(len(rows), 5001)
        self.assertEqual(queries[1]['$offset'], 5000)

    def test_property_cli_stdout_is_one_json_document(self):
        result = {'schema_version': '1.0', 'status': 'complete', 'pluto': {}, 'errors': []}
        with patch.object(screen, 'collect', return_value=result), contextlib.redirect_stdout(io.StringIO()) as output:
            code = screen.main(['--bbl', '3010100021'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), result)


if __name__ == '__main__':
    unittest.main()
