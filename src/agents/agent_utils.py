# Helper utilities for agent operations, including model selection.

import logging
from typing import List, Optional, Dict, Any

# Assuming ModelInfo and PerformanceTracker structures.
# from src.config.model_registry import ModelInfo # If strictly typed
# from src.agents.performance_tracker import PerformanceTracker # If strictly typed

logger = logging.getLogger(__name__)

# Define a type alias for enriched model info for clarity, if needed
# EnrichedModelInfo = Dict[str, Any] # or a TypedDict if preferred

DEFAULT_PERFORMANCE_SCORE = 0.0  # For models without performance data
DEFAULT_NUM_PARAMETERS = 0       # For models without parameter data, treat as smallest

def sort_models_by_size_performance_id(
    model_infos: List[Dict[str, Any]], # Expects list of ModelInfo-like dicts
    performance_metrics: Optional[Dict[str, Dict[str, Any]]] = None # provider -> model_id -> metrics
) -> List[Dict[str, Any]]:
    """
    Sorts a list of model information objects/dictionaries based on:
    1. Number of parameters (descending, None/0 treated as smallest).
    2. Performance score (descending, models not in tracker or with no score treated as lowest).
    3. Model ID (alphabetical ascending for tie-breaking).

    Args:
        model_infos: A list of dictionaries, where each dictionary represents a model
                     and is expected to have at least an "id" key, and optionally
                     "num_parameters" (int) and "provider" (str).
        performance_metrics: A dictionary structured as {provider_name: {model_id: {"score": float, ...}}}.
                             If None, performance sorting is skipped (effectively all scores are equal).

    Returns:
        A new list of model information dictionaries, sorted according to the criteria.
        Each dictionary in the returned list will have an added "performance_score" field.
    """
    if not model_infos:
        return []

    enriched_models = []
    for model_info in model_infos:
        enriched_model = model_info.copy()
        
        # Ensure 'id' and 'provider' are present, provide defaults if critical for sorting/lookup
        if 'id' not in enriched_model:
            logger.warning(f"Model info missing 'id', skipping: {enriched_model}")
            continue
        
        provider = enriched_model.get("provider", "unknown_provider_for_sort")

        # Get performance score
        score = DEFAULT_PERFORMANCE_SCORE
        if performance_metrics and provider in performance_metrics:
            model_specific_metrics = performance_metrics[provider].get(enriched_model["id"])
            if model_specific_metrics and "score" in model_specific_metrics:
                score = model_specific_metrics["score"]
        enriched_model["performance_score"] = score
        
        # Get num_parameters, ensure it's comparable
        num_params = enriched_model.get("num_parameters")
        if not isinstance(num_params, (int, float)) or num_params is None:
            num_params = DEFAULT_NUM_PARAMETERS 
        enriched_model["num_parameters_sortable"] = num_params # Store sortable version

        enriched_models.append(enriched_model)

    # Sort the models
    # Primary: num_parameters (descending) - None/0 treated as smallest.
    # Secondary: performance_score (descending) - DEFAULT_PERFORMANCE_SCORE treated as lowest.
    # Tertiary: model ID (alphabetical ascending).
    def sort_key(model: Dict[str, Any]):
        return (
            -model["num_parameters_sortable"], # Negative for descending
            -model["performance_score"],       # Negative for descending
            model["id"]                        # Ascending
        )

    sorted_models = sorted(enriched_models, key=sort_key)
    
    logger.debug(f"Sorted models ({len(sorted_models)}):")
    for m in sorted_models:
        logger.debug(f"  - ID: {m['id']}, Size: {m['num_parameters_sortable']}, Score: {m['performance_score']:.2f}, Provider: {m.get('provider')}")
        
    return sorted_models

if __name__ == '__main__':
    # Example Usage / Basic Test
    print("Running agent_utils.py example...")
    logging.basicConfig(level=logging.DEBUG)

    sample_models_data = [
        {"id": "model_A", "provider": "p1", "num_parameters": 7_000_000_000},
        {"id": "model_B", "provider": "p1", "num_parameters": 13_000_000_000},
        {"id": "model_C", "provider": "p1", "num_parameters": 7_000_000_000},
        {"id": "model_D", "provider": "p2"}, # Missing num_parameters
        {"id": "model_E", "provider": "p2", "num_parameters": 3_000_000_000},
        {"id": "model_F", "provider": "p1", "num_parameters": 13_000_000_000}, # Same size as B
    ]

    sample_perf_metrics = {
        "p1": {
            "model_A": {"score": 0.90, "latency": 100},
            "model_B": {"score": 0.85, "latency": 150},
            "model_C": {"score": 0.95, "latency": 90}, # Higher score than A, same size
            "model_F": {"score": 0.92, "latency": 120}, # Higher score than B, same size
        },
        "p2": {
            "model_D": {"score": 0.70, "latency": 200},
            "model_E": {"score": 0.75, "latency": 180},
        }
    }

    print("\n--- Sorting with performance metrics ---")
    sorted_list_with_perf = sort_models_by_size_performance_id(sample_models_data, sample_perf_metrics)
    # Expected order:
    # 1. model_F (13B, 0.92)
    # 2. model_B (13B, 0.85)
    # 3. model_C (7B, 0.95)
    # 4. model_A (7B, 0.90)
    # 5. model_E (3B, 0.75)
    # 6. model_D (0 params default, 0.70 score)

    print("\n--- Sorting without performance metrics (uses default score 0.0) ---")
    sorted_list_no_perf = sort_models_by_size_performance_id(sample_models_data, None)
    # Expected order (size then ID):
    # 1. model_B (13B)
    # 2. model_F (13B)
    # 3. model_A (7B)
    # 4. model_C (7B)
    # 5. model_E (3B)
    # 6. model_D (0 params default)

    print("\n--- Sorting with some models missing performance data ---")
    partial_perf_metrics = {
        "p1": {
            "model_A": {"score": 0.90, "latency": 100},
            # model_B, model_C, model_F missing scores for p1
        },
        "p2": {
            "model_E": {"score": 0.75, "latency": 180},
            # model_D missing score for p2
        }
    }
    sorted_list_partial_perf = sort_models_by_size_performance_id(sample_models_data, partial_perf_metrics)
    # Expected order: (Size DESC, Perf DESC (0.0 for missing), ID ASC)
    # 1. model_B (13B, 0.0)
    # 2. model_F (13B, 0.0)
    # 3. model_A (7B, 0.90)
    # 4. model_C (7B, 0.0)
    # 5. model_E (3B, 0.75)
    # 6. model_D (0B, 0.0)

    print("\n--- Sorting with models completely missing from performance data provider ---")
    missing_provider_perf_metrics = {
        "p1": { # p2 models will get default score
            "model_A": {"score": 0.90},
            "model_B": {"score": 0.85},
            "model_C": {"score": 0.95},
            "model_F": {"score": 0.92},
        }
    }
    sorted_list_missing_provider = sort_models_by_size_performance_id(sample_models_data, missing_provider_perf_metrics)
    # Expected:
    # 1. model_F (13B, 0.92)
    # 2. model_B (13B, 0.85)
    # 3. model_C (7B, 0.95)
    # 4. model_A (7B, 0.90)
    # 5. model_E (3B, 0.0) <- p2 model, gets default score
    # 6. model_D (0B, 0.0) <- p2 model, gets default score
    print("Done with agent_utils.py example.")
