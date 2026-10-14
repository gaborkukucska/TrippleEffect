import unittest
from src.utils.text_utils import extract_keywords_from_text, MIN_KEYWORD_LENGTH, DEFAULT_MAX_KEYWORDS

class TestExtractKeywords(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(extract_keywords_from_text(""), [])

    def test_string_with_only_stop_words(self):
        self.assertEqual(extract_keywords_from_text("the is a of and to in"), [])

    def test_simple_valid_string(self):
        # Assuming default MIN_KEYWORD_LENGTH=3, DEFAULT_MAX_KEYWORDS=5
        # Stop words: "this", "is", "a", "for"
        # Remaining: "test", "string", "keyword", "extraction" (test is repeated)
        # Expected: 'keyword', 'extraction', 'string', 'test' (order by frequency, then original if tied)
        # The current implementation does not guarantee order beyond frequency.
        # So, we'll check for set equality for keywords.
        expected_keywords = ['test', 'keyword', 'extraction', 'string']
        actual_keywords = extract_keywords_from_text("This is a test string for keyword extraction test")
        self.assertCountEqual(actual_keywords, expected_keywords)


    def test_string_with_punctuation(self):
        expected_keywords = ['keyword', 'test', 'another']
        actual_keywords = extract_keywords_from_text("keyword! test, another. keyword.")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_with_mixed_case(self):
        expected_keywords = ['keyword', 'test']
        actual_keywords = extract_keywords_from_text("Keyword Test KEYWORD test")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_shorter_than_max_keywords(self):
        # max_keywords is 5 by default
        expected_keywords = ['apple', 'banana', 'cherry']
        actual_keywords = extract_keywords_from_text("Apple banana cherry")
        self.assertCountEqual(actual_keywords, expected_keywords)
        self.assertEqual(len(actual_keywords), 3)

    def test_string_with_words_shorter_than_min_length(self):
        # MIN_KEYWORD_LENGTH is 3 by default
        self.assertEqual(extract_keywords_from_text("go run do it if or"), [])
        # Test with some valid words mixed in
        expected_keywords = ['valid']
        actual_keywords = extract_keywords_from_text("go run valid do it if or")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_with_duplicates_and_frequency(self):
        # Expected: 'common' (3), 'word' (2), 'another' (1), 'example' (1), 'set' (1) - top 5
        text = "common common common word word another example set"
        # Order depends on Counter's behavior for ties, which can be insertion order for Python 3.7+
        # For safety, we check for the presence and count.
        actual_keywords = extract_keywords_from_text(text, max_keywords=DEFAULT_MAX_KEYWORDS)
        self.assertIn('common', actual_keywords)
        self.assertIn('word', actual_keywords)
        
        # To be more precise, let's check the top N by count
        # The function returns a list, not a Counter object.
        # We assume 'common' appears most, then 'word'.
        self.assertEqual(actual_keywords[0], 'common')
        self.assertEqual(actual_keywords[1], 'word')
        self.assertEqual(len(actual_keywords), 5) # Should pick 5 most frequent
        self.assertCountEqual(actual_keywords, ['common', 'word', 'another', 'example', 'set'])


    def test_string_with_hyphenated_words(self):
        # 'knowledge-base' and 'long-term' should be treated as single keywords
        expected_keywords = ['user-interface', 'knowledge-base', 'long-term-strategy']
        actual_keywords = extract_keywords_from_text("Test with hyphens: user-interface knowledge-base long-term-strategy.")
        self.assertCountEqual(actual_keywords, expected_keywords)
        
        # Test leading/trailing hyphens (should be removed)
        expected_keywords_cleaned = ['word']
        actual_keywords_cleaned = extract_keywords_from_text("-word- another -word-")
        self.assertCountEqual(actual_keywords_cleaned, ['word', 'another'])


    def test_string_with_numbers(self):
        # Numbers should be filtered out if they are purely numeric
        expected_keywords = ['words', 'word1', 'word2']
        actual_keywords = extract_keywords_from_text("Test with numbers 123 and words like word1 word2 456.")
        self.assertCountEqual(actual_keywords, expected_keywords)
        
        # Test purely numeric string
        self.assertEqual(extract_keywords_from_text("123 456 7890"), [])

    def test_string_with_leading_trailing_multiple_spaces(self):
        expected_keywords = ['leading', 'trailing', 'spaces', 'multiple', 'between', 'words']
        actual_keywords = extract_keywords_from_text("  leading and trailing spaces   and multiple   spaces between words  ")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_max_keywords_parameter(self):
        text = "one two three four five six seven"
        actual_keywords_3 = extract_keywords_from_text(text, max_keywords=3)
        self.assertEqual(len(actual_keywords_3), 3)
        self.assertCountEqual(actual_keywords_3, ['one', 'two', 'three']) # Assuming simple order for non-repeated, non-stop words

        actual_keywords_0 = extract_keywords_from_text(text, max_keywords=0)
        self.assertEqual(len(actual_keywords_0), 0)

    def test_real_example_from_prompt(self):
        text = "Thought: The project plan needs refinement. Key areas include task delegation and timeline adjustment. We should also consider resource allocation for the new UI development phase."
        expected = ["project", "plan", "refinement", "areas", "include"] # Top 5 by default
        # Actual frequencies: project(1), plan(1), needs(1), refinement(1), key(1), areas(1), include(1), task(1), delegation(1), timeline(1), adjustment(1), consider(1), resource(1), allocation(1), new(1), development(1), phase(1)
        # All are 1, so it will be the first 5 non-stop words of sufficient length
        actual = extract_keywords_from_text(text)
        self.assertCountEqual(actual, expected)

    def test_non_alphanumeric_removal(self):
        text = "non-alphanumeric!@# characters like !@#$ should be removed."
        expected = ["non-alphanumeric", "characters", "like", "should", "removed"]
        actual = extract_keywords_from_text(text)
        self.assertCountEqual(actual, expected)

if __name__ == '__main__':
    unittest.main()
