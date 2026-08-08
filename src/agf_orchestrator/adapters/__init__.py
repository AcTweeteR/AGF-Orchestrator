"""Provider-neutral Director adapters."""

from .base import DirectorAdapter
from .codex import CodexAdapter
from .mock import MockAdapter
from .ollama import OllamaOpenHandsAdapter, OllamaProviderError, detect_local_model
from .openhands import OpenHandsAdapter, OpenHandsSDKAdapter

__all__ = [
    "CodexAdapter",
    "DirectorAdapter",
    "MockAdapter",
    "OpenHandsAdapter",
    "OpenHandsSDKAdapter",
    "OllamaOpenHandsAdapter",
    "OllamaProviderError",
    "detect_local_model",
]
