class ToolCreationError(Exception):
    """Custom exception for tool development failures"""
    def __init__(self, errors: list):
        super().__init__("Tool validation failed")
        self.errors = errors

class APIError(Exception):
    """Base class for API communication errors"""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code

class ResourceExhaustedError(Exception):
    """Raised when resource limits are exceeded"""
    def __init__(self, resource_type: str):
        super().__init__(f"{resource_type} limit exceeded")
