"""Housing source contracts, geographic edge cases, and failure behavior; offline."""
import contextlib
import io
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import housing_developments as housing


def hpd_row(**overrides):
    return {"project_id": "123", "building_id": "456", "project_name": "Example Housing",
            "latitude": "40.7", "longitude": "-74", "bbl": "3010100021",
            "all_counted_units": "0", "total_units": "40", **overrides}


def polygon(west=-74.001, east=-73.999, south=40.699, north=40.701):
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south],
            [east, north], [west, north], [west, south]]]}


class GeographyTests(unittest.TestCase):
    def test_large_development_is_found_by_near_edge_instead_of_centroid(self):
        row = {"tds_num": "1", "developmen": "Example Houses",
               "the_geom": polygon(west=-73.999, east=-73.95)}
        result = housing.normalize("nycha", row, 40.7, -74, 0.1)
        self.assertIsNotNone(result)
        self.assertLess(result["distance_mi"], 0.1)
        self.assertEqual(result["location"]["lon"], -73.999)

    def test_inside_hole_and_multipart_distances(self):
        shape = polygon()
        shape["coordinates"].append(polygon(-74.0005, -73.9995, 40.6995, 40.7005)["coordinates"][0])
        point = housing.nearest_point(shape, 40.7, -74)
        self.assertGreater(housing.distance_mi(40.7, -74, *point), 0)
        shape = {"type": "MultiPolygon", "coordinates": [shape["coordinates"], polygon(-74.1, -74.09)["coordinates"]]}
        self.assertEqual(housing.nearest_point(shape, 40.7, -74.095), (40.7, -74.095))

    def test_bbox_corners_are_excluded_by_final_circle(self):
        south, north, west, east = housing.bbox(40.7, -74, 0.5)
        row = hpd_row(latitude=str(north), longitude=str(east))
        self.assertIsNone(housing.normalize("hpd", row, 40.7, -74, 0.5))

    def test_prefers_tax_lot_coordinates_and_falls_back_to_address(self):
        row = hpd_row(latitude_internal="40.7001", longitude_internal="-74.0001")
        self.assertEqual(housing.coordinates(row), (40.7001, -74.0001, "tax_lot_centroid"))
        row["latitude_internal"] = "NaN"
        self.assertEqual(housing.coordinates(row), (40.7, -74, "address_estimate"))
        self.assertIsNone(housing.coordinates({"latitude": "0", "longitude": "0"}))


class RecordTests(unittest.TestCase):
    def test_missing_units_stay_null_and_zero_stays_zero(self):
        record = housing.normalize("hpd", hpd_row(), 40.7, -74, 0.5)
        self.assertEqual(record["units"]["all_counted_units"], 0)
        self.assertIsNone(record["units"]["low_income_units"])
        self.assertIsNone(record["building_completion_date"])

    def test_same_building_in_different_projects_retains_distinct_ids(self):
        first = housing.normalize("hpd", hpd_row(), 40.7, -74, 0.5)
        second = housing.normalize("hpd", hpd_row(project_id="789"), 40.7, -74, 0.5)
        self.assertNotEqual(first["source_id"], second["source_id"])

    def test_missing_location_is_not_invented(self):
        self.assertIsNone(housing.normalize("hpd", {"project_id": "1"}, 40.7, -74, 0.5))


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.spec = housing.SOURCES["hpd"]
        self.metadata = {"rowsUpdatedAt": 123, "columns": [
            {"fieldName": field} for field in self.spec["fields"].split(",")]}

    def test_pagination_reads_beyond_one_page(self):
        responses = [self.metadata, [{"n": "3"}], [hpd_row(), hpd_row(project_id="2")],
                     [hpd_row(project_id="3")], self.metadata]
        with patch.object(housing, "get_json", side_effect=responses) as get:
            rows, _ = housing.fetch_rows(Mock(), self.spec, "1=1", page_size=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(get.call_args_list[3].args[2]["$offset"], 2)

    def test_truncation_and_source_edits_fail_instead_of_looking_complete(self):
        for responses in (
            [self.metadata, [{"n": "2"}], [hpd_row()], []],
            [self.metadata, [{"n": "1"}], [hpd_row()], {**self.metadata, "rowsUpdatedAt": 124}],
        ):
            with self.subTest(responses=responses), patch.object(housing, "get_json", side_effect=responses):
                with self.assertRaises(housing.SourceError):
                    housing.fetch_rows(Mock(), self.spec, "1=1", page_size=1)

    def test_missing_schema_or_malformed_metadata_fails_cleanly(self):
        for metadata in ([], {"rowsUpdatedAt": 123, "columns": []}, {"columns": self.metadata["columns"]}):
            with self.subTest(metadata=metadata), patch.object(housing, "get_json", return_value=metadata):
                with self.assertRaises(housing.SourceError):
                    housing.fetch_rows(Mock(), self.spec, "1=1")

    def test_failed_source_is_explicit_and_other_source_still_returns(self):
        with patch.object(housing, "fetch_rows", side_effect=[housing.SourceError("Unavailable"), ([hpd_row()], "2026-01-01")]), \
                patch.object(housing, "count_rows", return_value=12):
            result = housing.collect(Mock(), 40.7, -74, 0.5)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"]["nycha"]["status"], "failed")
        self.assertEqual(result["sources"]["hpd"]["citywide_records_without_coordinates"], 12)
        self.assertEqual(len(result["records"]), 1)

    def test_empty_success_differs_from_total_failure(self):
        for failure, expected in [(False, "complete"), (True, "failed")]:
            with self.subTest(failure=failure), patch.object(housing, "fetch_rows",
                    **({"side_effect": housing.SourceError("Unavailable")} if failure else {"return_value": ([], "2026-01-01")})), \
                    patch.object(housing, "count_rows", return_value=0):
                result = housing.collect(Mock(), 40.7, -74, 0.5)
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["records"], [])

    def test_duplicate_identifiers_fail_source_without_partial_records(self):
        with patch.object(housing, "fetch_rows", return_value=([hpd_row(), hpd_row()], "2026-01-01")):
            result = housing.collect(Mock(), 40.7, -74, 0.5, "hpd")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["records"], [])

    def test_bad_inputs_make_no_source_requests(self):
        for lat, lon, radius in [(math.nan, -74, 1), (40.7, math.inf, 1), (40.7, -74, 0), (40.7, -74, 26)]:
            with self.subTest(values=(lat, lon, radius)), patch.object(housing, "fetch_rows") as fetch:
                with self.assertRaises(ValueError):
                    housing.collect(Mock(), lat, lon, radius)
                fetch.assert_not_called()

    def test_address_quote_is_escaped_in_source_query(self):
        row = {"address": "1 O'NEILL STREET", "latitude": "40.7", "longitude": "-74", "bbl": "3010100021"}
        with patch.object(housing, "get_json", return_value=[row]) as get:
            housing.resolve_origin(Mock(), "1 O'Neill Street", "BK")
        self.assertIn("O''NEILL", get.call_args.args[2]["$where"])

    def test_cli_emits_one_json_document_and_nonzero_for_partial_results(self):
        with patch.object(housing, "fetch_rows", side_effect=[housing.SourceError("Unavailable"), ([], "2026-01-01")]), \
                patch.object(housing, "count_rows", return_value=0), contextlib.redirect_stdout(io.StringIO()) as out:
            code = housing.main(["--lat", "40.7", "--lon", "-74"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["status"], "partial")


if __name__ == "__main__":
    unittest.main()
