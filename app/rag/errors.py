class GoogleRAGConfigurationError(RuntimeError):
    """Raised when Google RAG configuration is incomplete or invalid."""


class GoogleRAGRetrievalError(RuntimeError):
    """Raised when Discovery Engine retrieval fails."""


class GeminiJSONError(RuntimeError):
    """Raised when Gemini does not return valid structured JSON."""
