# START OF FILE tests/unit/test_text_utils.py
"""
Tests for extract_keywords_from_text().

Round 2m: the expectations of 9 tests did not match the function's rules (they expected stop words such as 'should',
'between' and 'another' as keywords, more keywords than DEFAULT_MAX_KEYWORDS allows, and 'this' to be a stop word while
it was not). The rules are: lower case; punctuation removed except hyphens inside words; stop words, words shorter
than MIN_KEYWORD_LENGTH and purely numeric words dropped; the most frequent words first (ties in order of first
appearance); at most max_keywords words. 'this', 'that', 'these' and 'those' are stop words since Round 2m.
"""
import unittest

from src.utils.text_utils import DEFAULT_MAX_KEYWORDS, MIN_KEYWORD_LENGTH, STOP_WORDS, extract_keywords_from_text


class TestExtractKeywords(unittest.TestCase):

    def test_defaults(self):
        self.assertEqual(MIN_KEYWORD_LENGTH, 3)
        self.assertEqual(DEFAULT_MAX_KEYWORDS, 5)

    def test_empty_string(self):
        self.assertEqual(extract_keywords_from_text(""), [])

    def test_string_with_only_stop_words(self):
        self.assertEqual(extract_keywords_from_text("the is a of and to in"), [])

    def test_simple_valid_string(self):
        # 'this', 'is', 'a', 'for' are stop words; 'test' occurs twice and comes first
        actual = extract_keywords_from_text("This is a test string for keyword extraction test")
        self.assertEqual(actual, ["test", "string", "keyword", "extraction"])

    def test_string_with_punctuation(self):
        # 'another' is a stop word
        self.assertEqual(extract_keywords_from_text("keyword! test, another. keyword."), ["keyword", "test"])

    def test_string_with_mixed_case(self):
        self.assertEqual(extract_keywords_from_text("Keyword Test KEYWORD test"), ["keyword", "test"])

    def test_string_shorter_than_max_keywords(self):
        self.assertEqual(extract_keywords_from_text("Apple banana cherry"), ["apple", "banana", "cherry"])

    def test_words_shorter_than_min_length(self):
        # 'go', 'do', 'it', 'if', 'or' are too short (or stop words); 'run' has exactly MIN_KEYWORD_LENGTH characters
        self.assertEqual(extract_keywords_from_text("go do it if or"), [])
        self.assertEqual(extract_keywords_from_text("go run do it if or"), ["run"])
        self.assertEqual(extract_keywords_from_text("go valid do it if or"), ["valid"])

    def test_string_with_duplicates_and_frequency(self):
        # 'another' and 'example' are stop words
        text = "common common common word word another example set"
        self.assertEqual(extract_keywords_from_text(text), ["common", "word", "set"])
        text = "common common common word word alpha beta gamma delta"
        self.assertEqual(extract_keywords_from_text(text), ["common", "word", "alpha", "beta", "gamma"])

    def test_string_with_hyphenated_words(self):
        actual = extract_keywords_from_text("Test with hyphens: user-interface knowledge-base long-term-strategy.")
        self.assertEqual(actual, ["test", "hyphens", "user-interface", "knowledge-base", "long-term-strategy"])
        # leading/trailing hyphens are removed; 'another' is a stop word
        self.assertEqual(extract_keywords_from_text("-word- another -word-"), ["word"])

    def test_string_with_numbers(self):
        # purely numeric words are dropped, words with digits are kept; at most 5 keywords
        actual = extract_keywords_from_text("Test with numbers 123 and words like word1 word2 456.")
        self.assertEqual(actual, ["test", "numbers", "words", "like", "word1"])
        self.assertEqual(extract_keywords_from_text("Test with numbers 123 and words like word1 word2 456.", max_keywords=6),
                         ["test", "numbers", "words", "like", "word1", "word2"])
        self.assertEqual(extract_keywords_from_text("123 456 7890"), [])

    def test_string_with_leading_trailing_multiple_spaces(self):
        # 'between' is a stop word; 'spaces' occurs twice
        actual = extract_keywords_from_text("  leading and trailing spaces   and multiple   spaces between words  ")
        self.assertEqual(actual, ["spaces", "leading", "trailing", "multiple", "words"])

    def test_max_keywords_parameter(self):
        text = "one two three four five six seven"
        self.assertEqual(extract_keywords_from_text(text, max_keywords=3), ["one", "two", "three"])
        self.assertEqual(extract_keywords_from_text(text, max_keywords=0), [])

    def test_real_example_from_prompt(self):
        text = ("Thought: The project plan needs refinement. Key areas include task delegation and timeline adjustment. "
                "We should also consider resource allocation for the new UI development phase.")
        # every word occurs once, so the first five keywords in order of appearance
        self.assertEqual(extract_keywords_from_text(text), ["thought", "project", "plan", "needs", "refinement"])

    def test_non_alphanumeric_removal(self):
        # 'should' and 'be' are stop words
        actual = extract_keywords_from_text("non-alphanumeric!@# characters like !@#$ should be removed.")
        self.assertEqual(actual, ["non-alphanumeric", "characters", "like", "removed"])

    def test_demonstratives_are_stop_words(self):
        for word in ("this", "that", "these", "those"):
            self.assertIn(word, STOP_WORDS)
        self.assertEqual(extract_keywords_from_text("This and that, these and those"), [])


if __name__ == '__main__':
    unittest.main()
