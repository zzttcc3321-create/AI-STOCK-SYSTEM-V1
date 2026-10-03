"""Injected model boundary. No supplier, default model, credentials or adapter."""

from typing import Any, Protocol

from .contracts import PromptEnvelope


class ModelExecutor(Protocol):
    @property
    def model_version(self) -> str:
        """Explicit identity of the executor's runtime model/version."""
        ...

    def generate(self, prompt_envelope: PromptEnvelope, output_schema: dict[str, Any]) -> str:
        """Return the original structured response as JSON text, once per run."""
        ...
