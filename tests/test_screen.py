"""Regression checks for Instinct's October 28 feedback; no network or Notion writes."""
import copy
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
os.environ.setdefault("NOTION_TOKEN", "offline-test-token")
import screen
import notion_api
from comp_review import apply_review, read_review
from listing_media import crop_floorplan, floorplan_candidates, download_image


class SourceTests(unittest.TestCase):
    def test_shared_directory_reuses_places_without_a_google_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data" / "amenities.json").write_text(json.dumps([
                {"source_id": "nyc-dohmh:1", "type": "Restaurant", "name": "Cafe", "lat": 40.7001, "lon": -74},
                {"source_id": "mta:A", "type": "Subway station", "name": "Station (A C)", "lat": 40.7002, "lon": -74},
            ]))
            with patch.object(screen, "ROOT", root), patch.object(screen, "nta_polygons", return_value=[]), patch.object(screen.SESSION, "post") as google:
                layers = screen.shared_area_layers({"latitude": "40.7", "longitude": "-74"}, 0.5)
            google.assert_not_called()
            self.assertEqual(layers["food"][0]["name"], "Cafe")
            lines = screen.area_items({"latitude": "40.7", "longitude": "-74"}, layers)
            self.assertTrue(any("Cafe" in line for _, line, _, _ in lines))
            self.assertFalse(any("None" in line or "0 ratings" in line for _, line, _, _ in lines))

    def test_local_env_does_not_override_exports_or_execute_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / ".env"
            p.write_text('GOOGLE_MAPS_API_KEY="local-value"\nNOTION_TOKEN=local-token\nUNRELATED=$(echo hello)\n')
            with patch.dict(screen.os.environ, {"NOTION_TOKEN": "exported"}, clear=True):
                screen.load_local_env(p)
                self.assertEqual(screen.os.environ["GOOGLE_MAPS_API_KEY"], "local-value")
                self.assertEqual(screen.os.environ["NOTION_TOKEN"], "exported")
                self.assertNotIn("UNRELATED", screen.os.environ)

    def test_bin_without_any_dob_or_hpd_activity(self):
        def source(ds, params, **kwargs):
            return [{"bin": "3022256"}, {"bin": "3000000"}, {"bin": "0"}] if ds == screen.DS["buildings"] else []
        with patch.object(screen, "soda", side_effect=source):
            facts = screen.dob_facts("3", "1010", "21", "3010100021")
        self.assertEqual(facts["bins"], ["3022256"])
        self.assertEqual(facts["bin_source"], "NYC building footprints")

    def test_unreachable_bin_source_stays_unknown(self):
        def source(ds, params, **kwargs):
            if ds == screen.DS["buildings"]:
                raise RuntimeError("unreachable")
            return []
        with patch.object(screen, "soda", side_effect=source):
            facts = screen.dob_facts("3", "1010", "21", "3010100021")
        self.assertEqual(facts["bins"], [])
        self.assertIsNone(facts["bin_source"])

    def test_food_rejects_retail_with_secondary_cafe(self):
        places = [{"displayName": {"text": name}, "primaryType": kind, "types": [kind, "cafe"],
                   "rating": 4.9, "userRatingCount": 1000, "location": {"latitude": 40, "longitude": -74}}
                  for name, kind in [("Whole Foods", "supermarket"), ("Barnes & Noble", "book_store"),
                                     ("Chela", "mexican_restaurant"), ("Cafe", "cafe"), ("Unknown", None)]]
        response = Mock(status_code=200)
        response.json.return_value = {"places": places}
        with patch.dict(screen.os.environ, {"GOOGLE_MAPS_API_KEY": "test-only"}), patch.object(screen.SESSION, "post", return_value=response):
            food = screen.places_food(40, -74)
        self.assertEqual([p["name"] for p in food], ["Chela", "Cafe"])

    def test_places_key_alias_is_used_for_places_only(self):
        response = Mock(status_code=200)
        response.json.return_value = {"places": []}
        with patch.dict(screen.os.environ, {"GOOGLE_PLACES_API_KEY": "places-test", "GOOGLE_MAPS_API_KEY": "maps-test"}, clear=True):
            with patch.object(screen.SESSION, "post", return_value=response) as post:
                screen.places_food(40, -74)
            self.assertEqual(post.call_args.kwargs["headers"]["X-Goog-Api-Key"], "places-test")


class MapTests(unittest.TestCase):
    def test_diagonal_nearby_park_keeps_its_edge_distance(self):
        # A large park can touch the edge of the square search area while its
        # nearest boundary point lies outside one side of that square.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            geom = {"type": "Polygon", "coordinates": [[[-73.989, 40.69], [-73.98, 40.69], [-73.98, 40.71], [-73.989, 40.71], [-73.989, 40.69]]]}
            (root / "data" / "amenities.json").write_text(json.dumps([
                {"source_id": "nyc-parks:test", "name": "Park", "type": "Park", "lat": 40.7, "lon": -73.985, "geometry": geom, "acres": 500}]))
            with patch.object(screen, "ROOT", root), patch.object(screen, "nta_polygons", return_value=[]):
                layers = screen.shared_area_layers({"latitude": "40.7", "longitude": "-74"}, 0.5)
            self.assertEqual(len(layers["parks"]), 1)

    def test_distance_to_nearest_segment_not_centroid_or_vertex(self):
        geom = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
        lat, lon = screen.nearest_park_point(geom, -1, 1)
        self.assertAlmostEqual(lat, 0)
        self.assertAlmostEqual(lon, 1)
        self.assertEqual(screen.nearest_park_point(geom, 1, 1), (1, 1))

    def test_multipart_park_and_hole(self):
        outer = [[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]
        hole = [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]
        far = [[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]
        geom = {"type": "MultiPolygon", "coordinates": [[outer, hole], [far]]}
        point = screen.nearest_park_point(geom, 1.5, 1.5)
        self.assertNotEqual(point, (1.5, 1.5))
        self.assertLess(screen.haversine_mi(1.5, 1.5, *point), 40)

    def test_twenty_coincident_pins_stay_readable_inside_map(self):
        positions = [(250, 250)]
        for _ in range(20):
            position = screen.pin_position(250, 250, positions, 500, 500, 48)
            self.assertTrue(all(math.dist(position, old) >= 48 for old in positions))
            self.assertTrue(all(24 <= value <= 476 for value in position))
            positions.append(position)


class ReviewTests(unittest.TestCase):
    def test_listing_evidence_moves_sale_and_is_scoped_to_date(self):
        comps = [{"bbl": "3000000001", "date": date, "reno": "permit signal"} for date in ("2025-01-01", "2026-01-01")]
        original = copy.deepcopy(comps)
        review = {"sales": [{"bbl": "3000000001", "date": "2025-01-01", "condition": "renovated"}]}
        purchase, exits, _ = apply_review(comps, review)
        self.assertEqual([c["date"] for c in purchase], ["2026-01-01"])
        self.assertEqual([c["date"] for c in exits], ["2025-01-01"])
        self.assertEqual(comps, original)

    def test_dob_signal_alone_does_not_prove_renovation(self):
        comps = [{"bbl": "3000000001", "date": "2025-01-01", "reno": "permit signal"}]
        purchase, exits, _ = apply_review(comps, {"sales": []})
        self.assertEqual(len(purchase), 1)
        self.assertEqual(exits, [])

    def test_review_can_hold_selection_without_backfilling_unreviewed_sales(self):
        purchase, _, excluded = apply_review([{"bbl": "3000000001", "date": "2025-01-01"}], {"sales": [], "include_unreviewed": False})
        self.assertEqual(purchase, [])
        self.assertEqual(excluded[0]["review_status"], "unreviewed")

    def test_condition_decision_requires_source_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "review.json"
            p.write_text(json.dumps({"sales": [{"bbl": "3000000001", "date": "2025-01-01", "condition": "renovated"}]}))
            with self.assertRaises(ValueError):
                read_review(str(p))


class MediaTests(unittest.TestCase):
    @unittest.skipUnless(screen.have_maps(), "Pillow optional")
    def test_crop_retains_both_drawing_bands_and_removes_branding(self):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(im)
        draw.rectangle((20, 20, 200, 45), fill="black")
        draw.rectangle((100, 160, 500, 340), outline="black", width=4)
        draw.rectangle((40, 400, 550, 580), outline="black", width=4)
        draw.rectangle((20, 720, 500, 740), fill="black")
        result = crop_floorplan(im)
        self.assertLess(result.height, 500)
        self.assertGreater(result.height, 420)
        self.assertGreater(result.width, 510)

    def test_floorplan_label_and_structured_media(self):
        html = '<img src="/plan.webp" alt="Floor plan 1"><img src="/photo.jpg" alt="photo 1">'
        html += '<script type="application/json">{"floorPlans":[{"url":"/second.png"}]}</script>'
        self.assertEqual(floorplan_candidates(html, "https://example.com/listing"), ["https://example.com/plan.webp", "https://example.com/second.png"])

    def test_description_mention_does_not_turn_all_photos_into_floorplans(self):
        html = '<script type="application/ld+json">{"description":"See floor plan", "url":"https://example.com/listing", "image":{"url":"https://example.com/photo.jpg"}}</script>'
        self.assertEqual(floorplan_candidates(html, "https://example.com"), [])

    @unittest.skipUnless(screen.have_maps(), "Pillow optional")
    def test_image_decoder_rejects_html(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "bad.jpg"
            p.write_text("<html>blocked</html>")
            with self.assertRaises(OSError):
                download_image(str(p), Path(directory) / "out.png")


class NotionRefreshTests(unittest.TestCase):
    def test_location_keeps_missing_coordinates_unknown(self):
        self.assertIsNone(notion_api.location_value("Unknown", None, None))
        self.assertIsNone(notion_api.location_value("Invalid", float("nan"), -74))
        self.assertEqual(notion_api.location_value("Known", "40.7", "-74")["place"]["lat"], 40.7)

    def test_transient_notion_update_failure_is_retried(self):
        responses = [Mock(status_code=503), Mock(status_code=200)]
        responses[1].json.return_value = {"id": "saved"}
        with patch.object(notion_api.requests, "request", side_effect=responses), patch.object(notion_api.time, "sleep"):
            self.assertEqual(notion_api.req("PATCH", "/pages/test", {"properties": {}}), {"id": "saved"})

    def test_refresh_preserves_manual_blocks_and_notes_ids(self):
        def block(i, kind, text=""):
            return {"id": str(i), "type": kind, kind: {"rich_text": [{"plain_text": text}]}}
        old = [block(1, "paragraph", "manual verdict"), block(2, "heading_3", "Details"),
               block(3, "table", "tax facts"), block(4, "heading_3", "Area"), block(5, "image"),
               block(6, "heading_3", "Purchase comps"), block(7, "table"),
               block(8, "heading_3", "Noah's notes"), block(9, "paragraph", "private note")]
        mutations = []
        def req(method, path, body=None, **kwargs):
            mutations.append((method, path, body))
            return {"results": [{"id": "new-block"}]}
        res = {"checked": "2026-10-28", "area_caption": "caption", "area_pins": [], "comps": [],
               "body_md": {"comps": "No qualifying comps."}}
        with patch.object(notion_api, "list_children", return_value=old), patch.object(notion_api, "req", side_effect=req):
            screen.notion_refresh_sections(res, "deal", None, None, [], Mock())
        deleted = [path for method, path, _ in mutations if method == "DELETE"]
        self.assertEqual(deleted, ["/blocks/5", "/blocks/7"])
        self.assertFalse(any(path in {"/blocks/1", "/blocks/2", "/blocks/3", "/blocks/8", "/blocks/9"} for _, path, _ in mutations))

    def test_upload_failure_leaves_existing_content(self):
        old = [{"id": str(i), "type": "heading_3", "heading_3": {"rich_text": [{"plain_text": text}]}}
               for i, text in enumerate(("Area", "Purchase comps", "Noah's notes"))]
        with patch.object(notion_api, "list_children", return_value=old), patch.object(notion_api, "req") as req:
            with self.assertRaises(RuntimeError):
                screen.notion_refresh_sections({"comps": []}, "deal", Path("area.png"), None, [], Mock(return_value=None))
        req.assert_not_called()

    def test_headings_below_first_notes_boundary_are_never_refresh_targets(self):
        old = [{"id": str(i), "type": "heading_3", "heading_3": {"rich_text": [{"plain_text": text}]}}
               for i, text in enumerate(("Area", "Noah's notes", "Purchase comps", "Noah's notes"))]
        with patch.object(notion_api, "list_children", return_value=old), patch.object(notion_api, "req") as req:
            with self.assertRaises(ValueError):
                screen.notion_refresh_sections({"comps": []}, "deal", None, None, [], Mock())
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
