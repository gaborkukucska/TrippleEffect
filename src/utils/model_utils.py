# START OF FILE src/utils/model_utils.py
import re
from typing import Optional

def _extract_model_size_b(model_id: Optional[str]) -> float:
    """
    Extracts the parameter size in billions (e.g., 7b, 70b, 0.5b) from a model ID string.
    Returns 0.0 if the pattern is not found or the input is invalid.
    """
    if not isinstance(model_id, str):
        return 0.0

    # Regex to find patterns like -7b-, -70B-, _0.5b_, _7b, -8B etc.
    # Handles optional decimal part, case-insensitive 'b', and common separators or end of string.
    # Prioritize patterns with separators first.
    match = re.search(r'[-_](\d+(?:\.\d+)?)[bB][-_]', model_id)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0 # Should not happen with regex, but safe fallback

    # Fallback for patterns at the end of the string
    match_end = re.search(r'[-_](\d+(?:\.\d+)?)[bB]$', model_id)
    if match_end:
        try:
            return float(match_end.group(1))
        except ValueError:
            return 0.0

    # Fallback for size directly appended without separator (less common, e.g., model7b)
    # match_direct = re.search(r'(\d+(?:\.\d+)?)[bB]$', model_id)
    # if match_direct:
    #     try: return float(match_direct.group(1))
    #     except ValueError: return 0.0

    return 0.0 # Return 0 if no size pattern found
# END OF FILE src/utils/model_utils.py