from django.test import SimpleTestCase

from ..formatting import format_inr
from ..parsing import AmountError, parse_inr


class ParseInrTests(SimpleTestCase):
    def test_whole_rupees(self):
        self.assertEqual(parse_inr("1234"), 123_400)

    def test_two_decimal_places(self):
        self.assertEqual(parse_inr("1234.56"), 123_456)

    def test_one_decimal_place(self):
        self.assertEqual(parse_inr("0.5"), 50)

    def test_indian_grouping(self):
        self.assertEqual(parse_inr("1,23,456.78"), 12_345_678)

    def test_rupee_sign_and_spaces(self):
        self.assertEqual(parse_inr("₹ 1,000"), 100_000)

    def test_non_breaking_space(self):
        self.assertEqual(parse_inr("1\u00a0000"), 100_000)

    def test_surrounding_whitespace(self):
        self.assertEqual(parse_inr("  42  "), 4_200)

    def test_zero(self):
        self.assertEqual(parse_inr("0"), 0)

    def test_rejects_three_decimal_places(self):
        with self.assertRaises(AmountError):
            parse_inr("12.345")

    def test_rejects_empty(self):
        for value in ("", "   ", None):
            with self.assertRaises(AmountError):
                parse_inr(value)

    def test_rejects_text(self):
        for value in ("abc", "1.2.3", "1e3", "₹"):
            with self.assertRaises(AmountError):
                parse_inr(value)

    def test_rejects_negative_by_default(self):
        with self.assertRaises(AmountError):
            parse_inr("-5")

    def test_allows_negative_when_asked(self):
        self.assertEqual(parse_inr("-5", allow_negative=True), -500)

    def test_round_trip_with_formatting(self):
        self.assertEqual(parse_inr(format_inr(12_345_678).lstrip("₹")), 12_345_678)
