"""Accuracy-gate regressions using synthetic schedules; no real PDF or API key."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.verify_accuracy import audit, canonical


def fixture():
    row = {
        "subject": "TEST 101", "class_time": "08:00-09:00 AM", "class_days": "MWF",
        "course_year": "BS TEST 1-A", "instructor": "Teacher, A.", "examiner": "Examiner, B.",
        "exam_room": None, "exam_building": "CAB", "exam_day": "May 11, 2026",
        "exam_time": "01:00-02:00 PM", "major_exam": "Finals", "semester": "Second Semester",
        "academic_year": "2025-2026",
    }
    extraction = {"pdf_sha256": "synthetic", "pages": {"1": [], "2": [copy.deepcopy(row)]}}
    review = {"pdf_sha256": "synthetic", "reviewed_against_page_images": True,
              "row_counts": {"1": 0, "2": 1},
              "checks": [{"page": 2, "row": 1, "expected": copy.deepcopy(row)}]}
    return extraction, review


def check(extraction, review):
    return audit(extraction, review, "synthetic", 2)


class AccuracyGateTests(unittest.TestCase):
    def test_complete_review_passes_only_when_all_13_fields_and_pages_match(self):
        report = check(*fixture())
        self.assertTrue(report["all_correct"])
        self.assertTrue(report["full_reference_reviewed"])
        self.assertEqual(report["fields_reviewed"], 13)
        self.assertEqual(report["unreviewed_fields"], 0)

    def test_matching_partial_review_cannot_certify_untested_fields(self):
        actual, review = fixture()
        review["checks"][0]["expected"] = {"subject": "TEST 101"}
        actual["pages"]["2"][0]["exam_building"] = "WRONG"
        report = check(actual, review)
        self.assertEqual(report["differences"], [])
        self.assertFalse(report["all_correct"])
        self.assertEqual(report["unreviewed_fields"], 12)

    def test_matching_row_counts_alone_do_not_verify_accuracy(self):
        actual, review = fixture()
        review["checks"] = []
        report = check(actual, review)
        self.assertTrue(all(c["passed"] for c in report["row_count_checks"]))
        self.assertFalse(report["all_correct"])

    def test_wrong_subject_location_and_column_shift_fail_despite_same_row_count(self):
        actual, review = fixture()
        actual["pages"]["2"][0].update(subject="PREVIOUS 101", exam_building="CCIS",
                                        course_year="Teacher, A.")
        report = check(actual, review)
        self.assertFalse(report["all_correct"])
        self.assertEqual({d["field"] for d in report["differences"]},
                         {"subject", "exam_building", "course_year"})
        self.assertTrue(all(c["passed"] for c in report["row_count_checks"]))

    def test_explicit_source_date_correction_is_not_silently_ignored(self):
        actual, review = fixture()
        review["source_corrections"] = [{"page": 2, "expected": "May 11, 2026",
                                          "authority": "User correction"}]
        actual["pages"]["2"][0]["exam_day"] = "March 6, 2026 (Monday)"
        report = check(actual, review)
        self.assertEqual(report["differences"][0]["field"], "exam_day")
        self.assertEqual(report["source_corrections"], review["source_corrections"])
        self.assertFalse(report["all_correct"])

    def test_missing_row_fails_even_when_expected_cell_is_null(self):
        actual, review = fixture()
        actual["pages"]["2"] = []
        report = check(actual, review)
        self.assertEqual(len(report["differences"]), 13)
        self.assertTrue(all(d["row_missing"] for d in report["differences"]))
        self.assertFalse(report["all_correct"])

    def test_extra_duplicate_row_cannot_pass(self):
        actual, review = fixture()
        actual["pages"]["2"] *= 2
        report = check(actual, review)
        self.assertTrue(report["full_reference_reviewed"])
        self.assertFalse(report["all_correct"])
        self.assertFalse(report["row_count_checks"][1]["passed"])

    def test_missing_cover_or_extra_blank_page_fails(self):
        for change in ("missing", "extra"):
            with self.subTest(change=change):
                actual, review = fixture()
                if change == "missing":
                    del actual["pages"]["1"]
                else:
                    actual["pages"]["3"] = []
                self.assertFalse(check(actual, review)["all_correct"])

    def test_equal_counts_do_not_hide_a_missing_row_replaced_by_duplicate(self):
        actual, review = fixture()
        second = copy.deepcopy(actual["pages"]["2"][0])
        second["subject"] = "TEST 202"
        review["row_counts"]["2"] = 2
        review["checks"].append({"page": 2, "row": 2, "expected": second})
        actual["pages"]["2"] *= 2
        report = check(actual, review)
        self.assertTrue(report["row_count_checks"][1]["passed"])
        self.assertFalse(report["all_correct"])
        self.assertEqual(report["differences"][0]["row"], 2)

    def test_review_of_only_some_pages_is_not_a_full_reference(self):
        actual, review = fixture()
        del review["row_counts"]["1"]
        report = check(actual, review)
        self.assertFalse(report["all_correct"])
        self.assertIsNone(report["fields_required"])

    def test_each_input_must_be_bound_to_the_same_pdf(self):
        for changed in (0, 1):
            with self.subTest(changed=changed):
                documents = fixture()
                documents[changed]["pdf_sha256"] = "different PDF"
                with self.assertRaises(ValueError):
                    check(*documents)

    def test_unreviewed_model_output_cannot_be_used_as_source_review(self):
        actual, review = fixture()
        review["reviewed_against_page_images"] = False
        with self.assertRaises(ValueError):
            check(actual, review)

    def test_duplicate_checks_cannot_inflate_coverage(self):
        actual, review = fixture()
        review["checks"].append(copy.deepcopy(review["checks"][0]))
        with self.assertRaises(ValueError):
            check(actual, review)

    def test_invalid_review_coordinates_and_fields_are_rejected(self):
        for mutation in ({"row": 0}, {"row": 2}, {"page": 3}, {"page": True},
                         {"expected": {"unsupported": "value"}}, {"expected": {"subject": 123}}):
            with self.subTest(mutation=mutation):
                actual, review = fixture()
                review["checks"][0].update(mutation)
                with self.assertRaises(ValueError):
                    check(actual, review)

    def test_invalid_extracted_cells_and_row_shapes_are_rejected(self):
        for mutation in ({"subject": 123}, {"unexpected": "value"}):
            with self.subTest(mutation=mutation):
                actual, review = fixture()
                actual["pages"]["2"][0].update(mutation)
                with self.assertRaises(ValueError):
                    check(actual, review)

    def test_null_string_is_not_a_missing_cell(self):
        actual, review = fixture()
        actual["pages"]["2"][0]["exam_room"] = "null"
        report = check(actual, review)
        self.assertFalse(report["all_correct"])
        self.assertEqual(report["differences"][0]["field"], "exam_room")

    def test_harmless_formatting_can_match_but_ocr_characters_remain_distinct(self):
        self.assertEqual(canonical("subject", "test101"), canonical("subject", "TEST 101"))
        self.assertEqual(canonical("exam_day", "May 11, 2026 (Monday)"),
                         canonical("exam_day", "2026-05-11"))
        self.assertEqual(canonical("exam_time", "1:00–2:00 P.M."),
                         canonical("exam_time", "13:00-14:00"))
        self.assertEqual(canonical("academic_year", "AY 2025–2026"),
                         canonical("academic_year", "2025-2026"))
        self.assertNotEqual(canonical("course_year", "BSIT 1-B"),
                            canonical("course_year", "BSPT 1-B"))
        self.assertNotEqual(canonical("exam_room", "100B"), canonical("exam_room", "1008"))
        self.assertNotEqual(canonical("exam_room", None), canonical("exam_room", "null"))


if __name__ == "__main__":
    unittest.main()
