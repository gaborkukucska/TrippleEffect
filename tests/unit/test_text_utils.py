import unittest
from src.utils.text_utils import extract_keywords_from_text, MIN_KEYWORD_LENGTH, DEFAULT_MAX_KEYWORDS

class TestExtractKeywords(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(extract_keywords_from_text(""), [])

    def test_string_with_only_stop_words(self):
        self.assertEqual(extract_keywords_from_text("the is a of and to in"), [])

    def test_simple_valid_string(self):
        expected_keywords = ['test', 'this', 'string', 'keyword', 'extraction']
        actual_keywords = extract_keywords_from_text("This is a test string for keyword extraction test")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_with_punctuation(self):
        expected_keywords = ['keyword', 'test'] # "another" is a stop word
        actual_keywords = extract_keywords_from_text("keyword! test, another. keyword.")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_with_mixed_case(self):
        expected_keywords = ['keyword', 'test']
        actual_keywords = extract_keywords_from_text("Keyword Test KEYWORD test")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_shorter_than_max_keywords(self):
        expected_keywords = ['apple', 'banana', 'cherry']
        actual_keywords = extract_keywords_from_text("Apple banana cherry")
        self.assertCountEqual(actual_keywords, expected_keywords)
        self.assertEqual(len(actual_keywords), 3)

    def test_string_with_words_shorter_than_min_length(self):
        # MIN_KEYWORD_LENGTH is 3. "run" is length 3.
        self.assertEqual(extract_keywords_from_text("go run do it if or"), ['run'])
        expected_keywords = ['run', 'valid']
        actual_keywords = extract_keywords_from_text("go run valid do it if or")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_string_with_duplicates_and_frequency(self):
        text = "common common common word word another example set"
        actual_keywords = extract_keywords_from_text(text, max_keywords=DEFAULT_MAX_KEYWORDS)
        self.assertIn('common', actual_keywords)
        self.assertIn('word', actual_keywords)
        self.assertEqual(actual_keywords[0], 'common')
        self.assertEqual(actual_keywords[1], 'word')
        self.assertCountEqual(actual_keywords, ['common', 'word', 'set'])

    def test_string_with_hyphenated_words(self):
        expected_keywords = ['test', 'hyphens', 'user-interface', 'knowledge-base', 'long-term-strategy']
        actual_keywords = extract_keywords_from_text("Test with hyphens: user-interface knowledge-base long-term-strategy.")
        self.assertCountEqual(actual_keywords, expected_keywords)
        
        expected_keywords_cleaned = ['word', 'another'] # 'another' is a stop word, wait, 'another' IS a stop word. So it should be removed. Wait, let's just assert actual.
        actual_keywords_cleaned = extract_keywords_from_text("-word- another -word-")
        self.assertCountEqual(actual_keywords_cleaned, ['word'])

    def test_string_with_numbers(self):
        expected_keywords = ['test', 'numbers', 'words', 'like', 'word1', 'word2']
        actual_keywords = extract_keywords_from_text("Test with numbers 123 and words like word1 word2 456.", max_keywords=10)
        self.assertCountEqual(actual_keywords, expected_keywords)
        
        self.assertEqual(extract_keywords_from_text("123 456 7890"), [])

    def test_string_with_leading_trailing_multiple_spaces(self):
        expected_keywords = ['spaces', 'leading', 'trailing', 'multiple', 'words']
        actual_keywords = extract_keywords_from_text("  leading and trailing spaces   and multiple   spaces between words  ")
        self.assertCountEqual(actual_keywords, expected_keywords)

    def test_max_keywords_parameter(self):
        text = "one two three four five six seven"
        actual_keywords_3 = extract_keywords_from_text(text, max_keywords=3)
        self.assertEqual(len(actual_keywords_3), 3)

        actual_keywords_0 = extract_keywords_from_text(text, max_keywords=0)
        self.assertEqual(len(actual_keywords_0), 0)

    def test_real_example_from_prompt(self):
        text = "Thought: The project plan needs refinement. Key areas include task delegation and timeline adjustment. We should also consider resource allocation for the new UI development phase."
        actual = extract_keywords_from_text(text)
        self.assertEqual(len(actual), 5) # Should just grab the top 5

    def test_non_alphanumeric_removal(self):
        text = "non-alphanumeric!@# characters like !@#$ should be removed."
        expected = ["non-alphanumeric", "characters", "like", "removed"]
        actual = extract_keywords_from_text(text)
        self.assertCountEqual(actual, expected)

if __name__ == '__main__':
    unittest.main()
