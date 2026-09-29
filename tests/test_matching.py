import unittest

from matching import Matcher, fingerprint, normalize, split_terms
from models import Keyword


def kw(term, i=1):
    return Keyword(i, term, normalize(term), 0.0)


class NormalizeTest(unittest.TestCase):
    def test_lowercase_accents_and_symbols(self):
        self.assertEqual(normalize("RTX-4090 Füße ß"), "rtx 4090 fusse ss")

    def test_letters_and_digits_are_split(self):
        self.assertEqual(normalize("rtx4090"), "rtx 4090")
        self.assertEqual(normalize("iPhone15Pro"), "iphone 15 pro")

    def test_x1_and_x13_stay_different(self):
        self.assertEqual(normalize("X1"), "x 1")
        self.assertEqual(normalize("X13"), "x 13")

    def test_empty(self):
        self.assertEqual(normalize("  --  "), "")


class MatcherTest(unittest.TestCase):
    def test_spelling_variants_match(self):
        m = Matcher([kw("RTX 4090")], [])
        for title in ("NVIDIA GeForce RTX4090 24GB", "Gigabyte rtx-4090 OC", "RTX 4090 Founders"):
            with self.subTest(title=title):
                self.assertIsNotNone(m.match(title))

    def test_different_model_does_not_match(self):
        m = Matcher([kw("RTX 4090")], [])
        self.assertIsNone(m.match("RTX 4080 Super"))

    def test_joined_and_split_words(self):
        self.assertIsNotNone(Matcher([kw("Game Boy")], []).match("Nintendo GameBoy Color"))
        self.assertIsNotNone(Matcher([kw("gameboy")], []).match("Nintendo Game Boy Pocket"))

    def test_word_order_does_not_matter(self):
        self.assertIsNotNone(Matcher([kw("ThinkPad X1")], []).match("Lenovo X1 Carbon ThinkPad"))

    def test_x1_does_not_match_x13(self):
        m = Matcher([kw("ThinkPad X1")], [])
        self.assertIsNotNone(m.match("Lenovo ThinkPad X1 Carbon Gen 9"))
        self.assertIsNone(m.match("Lenovo ThinkPad X13 Yoga"))

    def test_most_specific_keyword_wins(self):
        m = Matcher([kw("iPhone 15", 1), kw("iPhone 15 Pro", 2)], [])
        self.assertEqual(m.match("Apple iPhone 15 Pro 256GB").term, "iPhone 15 Pro")
        self.assertEqual(m.match("Apple iPhone 15 128GB").term, "iPhone 15")

    def test_excludes_block_the_match(self):
        m = Matcher([kw("iPhone 15 Pro")], ["cover", "Max"])
        self.assertIsNone(m.match("Cover per iPhone 15 Pro"))
        self.assertIsNone(m.match("iPhone 15 Pro Max 512GB"))
        self.assertIsNotNone(m.match("iPhone 15 Pro 256GB"))
        self.assertEqual(m.excluded_by("Cover iPhone"), "cover")

    def test_excludes_are_whole_words(self):
        m = Matcher([kw("iPhone")], ["cover"])
        self.assertIsNotNone(m.match("iPhone con covers"))

    def test_no_keywords(self):
        self.assertIsNone(Matcher([], []).match("qualsiasi cosa"))


class HelpersTest(unittest.TestCase):
    def test_fingerprint_ignores_case_and_punctuation(self):
        self.assertEqual(fingerprint("Mario_88", "iPhone 15 Pro!!"), fingerprint("mario_88 ", "iphone 15 pro"))

    def test_fingerprint_depends_on_seller(self):
        self.assertNotEqual(fingerprint("mario", "iPhone"), fingerprint("luca", "iPhone"))

    def test_fingerprint_does_not_contain_seller_name(self):
        self.assertNotIn("mario", fingerprint("mario", "iPhone"))

    def test_split_terms(self):
        self.assertEqual(split_terms("RTX 4090, Game Boy\nThinkPad X1; ,"), ["RTX 4090", "Game Boy", "ThinkPad X1"])


if __name__ == "__main__":
    unittest.main()
