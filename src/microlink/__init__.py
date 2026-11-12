"""Python client for the Microlink API."""

from .client import (
    Microlink,
    MicrolinkError,
    MicrolinkResponse,
    RateLimit,
    RateLimitError,
    flatten_params,
)

__all__ = ["Microlink", "MicrolinkError", "MicrolinkResponse", "RateLimit", "RateLimitError", "flatten_params"]
__version__ = "0.1.0"
