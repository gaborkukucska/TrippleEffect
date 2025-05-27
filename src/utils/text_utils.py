import re
from collections import Counter

# Common English stop words
STOP_WORDS = set([
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should", "can",
    "could", "may", "might", "must", "am", "i", "you", "he", "she", "it", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "his", "its", "our",
    "their", "mine", "yours", "hers", "ours", "theirs", "myself", "yourself",
    "himself", "herself", "itself", "ourselves", "themselves", "and", "but", "or",
    "nor", "for", "so", "yet", "if", "else", "then", "elsewhere", "whereas",
    "while", "unless", "because", "since", "until", "after", "before", "when",
    "whenever", "where", "wherever", "why", "how", "what", "which", "who",
    "whom", "whose", "with", "without", "within", "without", "among", "between",
    "into", "onto", "unto", "to", "from", "in", "out", "on", "off", "over",
    "under", "above", "below", "up", "down", "through", "around", "about",
    "against", "during", "regarding", "concerning", "respecting", "despite",
    "not", "no", "never", "ever", "always", "often", "sometimes", "usually",
    "rarely", "seldom", "actually", "really", "very", "just", "quite", "rather",
    "almost", "enough", "too", "also", "even", "still", "yet", "however",
    "therefore", "thus", "hence", "consequently", "furthermore", "moreover",
    "meanwhile", "subsequently", "subsequently", "namely", "specifically",
    "generally", "particularly", "especially", "example", "eg", "ie", "etc",
    "other", "another", "such", "several", "many", "few", "some", "any", "all",
    "most", "much", "less", "least", "more", "own", "same", "different", "various"
])

MIN_KEYWORD_LENGTH = 3
DEFAULT_MAX_KEYWORDS = 5

def extract_keywords_from_text(text: str, max_keywords: int = DEFAULT_MAX_KEYWORDS) -> list[str]:
    """
    Extracts keywords from a given text string.

    Args:
        text: The input string from which to extract keywords.
        max_keywords: The maximum number of keywords to return.

    Returns:
        A list of extracted keywords.
    """
    if not text:
        return []

    # Convert to lowercase
    text = text.lower()

    # Remove punctuation and non-alphanumeric characters (keeping spaces to split later)
    # Allow hyphens within words (e.g., "knowledge-base") but not leading/trailing
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip() # Normalize whitespace

    # Split into words
    words = text.split()

    # Filter out stop words and short words, also clean leading/trailing hyphens
    filtered_words = []
    for word in words:
        cleaned_word = word.strip('-') # Remove hyphens if they are at the start/end
        if cleaned_word and cleaned_word not in STOP_WORDS and len(cleaned_word) >= MIN_KEYWORD_LENGTH:
            # Further ensure it's not purely numeric or just hyphens after cleaning
            if not cleaned_word.isnumeric() and cleaned_word != '-' and any(c.isalnum() for c in cleaned_word):
                filtered_words.append(cleaned_word)
    
    if not filtered_words:
        return []

    # Count word frequencies
    word_counts = Counter(filtered_words)

    # Get the most common keywords
    most_common_keywords = [word for word, count in word_counts.most_common(max_keywords)]

    return most_common_keywords

if __name__ == '__main__':
    # Example usage for testing
    sample_text_1 = "This is a sample thought about knowledge base integration and agent performance."
    keywords_1 = extract_keywords_from_text(sample_text_1)
    print(f"Text: '{sample_text_1}'\nKeywords: {keywords_1}")

    sample_text_2 = "The agent needs to think about how to use the file system tool. File system access is critical."
    keywords_2 = extract_keywords_from_text(sample_text_2)
    print(f"Text: '{sample_text_2}'\nKeywords: {keywords_2}")

    sample_text_3 = "A short one."
    keywords_3 = extract_keywords_from_text(sample_text_3)
    print(f"Text: '{sample_text_3}'\nKeywords: {keywords_3}")
    
    sample_text_4 = "思考：如何更好地管理项目？项目管理工具的选择很重要。" # Chinese example
    keywords_4 = extract_keywords_from_text(sample_text_4)
    print(f"Text: '{sample_text_4}'\nKeywords: {keywords_4}")

    sample_text_5 = "--- This should be --- filtered --- and also this-is-a-word ---"
    keywords_5 = extract_keywords_from_text(sample_text_5)
    print(f"Text: '{sample_text_5}'\nKeywords: {keywords_5}")

    sample_text_6 = "123 456 7890"
    keywords_6 = extract_keywords_from_text(sample_text_6)
    print(f"Text: '{sample_text_6}'\nKeywords: {keywords_6}")

    sample_text_7 = "Thought: The project plan needs refinement. Key areas include task delegation and timeline adjustment. We should also consider resource allocation for the new UI development phase."
    keywords_7 = extract_keywords_from_text(sample_text_7, max_keywords=7)
    print(f"Text: '{sample_text_7}'\nKeywords: {keywords_7}")

    sample_text_8 = "This is a a a a a test test test test of of of the the the keyword keyword keyword extraction."
    keywords_8 = extract_keywords_from_text(sample_text_8)
    print(f"Text: '{sample_text_8}'\nKeywords: {keywords_8}")

    sample_text_9 = ""
    keywords_9 = extract_keywords_from_text(sample_text_9)
    print(f"Text: '{sample_text_9}'\nKeywords: {keywords_9}")
    
    sample_text_10 = "think execute plan" # All short words, but not stop words
    keywords_10 = extract_keywords_from_text(sample_text_10)
    print(f"Text: '{sample_text_10}'\nKeywords: {keywords_10}")

    sample_text_11 = "non-alphanumeric!@# characters like !@#$ should be removed."
    keywords_11 = extract_keywords_from_text(sample_text_11)
    print(f"Text: '{sample_text_11}'\nKeywords: {keywords_11}")

    sample_text_12 = "Test with hyphens: user-interface knowledge-base long-term-strategy."
    keywords_12 = extract_keywords_from_text(sample_text_12)
    print(f"Text: '{sample_text_12}'\nKeywords: {keywords_12}")

    sample_text_13 = "Test with numbers 123 and words like word1 word2 456."
    keywords_13 = extract_keywords_from_text(sample_text_13)
    print(f"Text: '{sample_text_13}'\nKeywords: {keywords_13}")

    sample_text_14 = "  leading and trailing spaces   and multiple   spaces between words  "
    keywords_14 = extract_keywords_from_text(sample_text_14)
    print(f"Text: '{sample_text_14}'\nKeywords: {keywords_14}")

    sample_text_15 = "Keywords: keyword1, keyword2. Summary: This is a test. Detail: More details."
    keywords_15 = extract_keywords_from_text(sample_text_15)
    print(f"Text: '{sample_text_15}'\nKeywords: {keywords_15}")
