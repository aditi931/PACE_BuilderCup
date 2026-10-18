"""Gemini wrapper: structured output, on-disk cache, and a mock mode for offline demos/tests.

Runtime AI is Gemini only. Never add another model provider here (see CLAUDE.md).
"""
import hashlib
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from .config import Settings

T = TypeVar("T", bound=BaseModel)
MockHandler = Callable[[str, dict], dict]


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings, mock_handlers: dict[str, MockHandler] | None = None):
        self.s = settings
        self.mock = settings.mock_llm
        self.mock_handlers = mock_handlers or {}
        self._client = None
        self.calls = 0
        self.cache_hits = 0
        settings.cache_dir.mkdir(parents=True, exist_ok=True)

    def _client_get(self):
        if self._client is None:
            from google import genai  # imported lazily so mock mode needs no credentials

            auth = self.s.resolved_auth
            if auth == "vertex_express":
                # Vertex AI express-mode keys ("AQ....") go to Vertex, not AI Studio
                self._client = genai.Client(vertexai=True, api_key=self.s.google_api_key)
            elif auth == "vertex_adc":
                if not self.s.gcp_project:
                    raise LLMError("No GOOGLE_API_KEY and no GOOGLE_CLOUD_PROJECT for Vertex AI (ADC).")
                self._client = genai.Client(vertexai=True, project=self.s.gcp_project, location=self.s.gcp_location)
            else:
                if not self.s.google_api_key:
                    raise LLMError("GOOGLE_API_KEY is not set.")
                self._client = genai.Client(api_key=self.s.google_api_key)
        return self._client

    def _key(self, task: str, prompt: str, schema: type[BaseModel], image: tuple[bytes, str] | None) -> str:
        h = hashlib.sha256()
        for part in (self.s.gemini_model, task, schema.__name__, prompt):
            h.update(part.encode())
        if image:
            h.update(hashlib.sha256(image[0]).digest())
        return h.hexdigest()[:32]

    def structured(
        self,
        task: str,
        prompt: str,
        schema: type[T],
        image: tuple[bytes, str] | None = None,
        mock_context: dict[str, Any] | None = None,
    ) -> T:
        if self.mock:
            handler = self.mock_handlers.get(task)
            if handler is None:
                raise LLMError(f"No mock handler for task '{task}'.")
            self.calls += 1
            return schema.model_validate(handler(prompt, mock_context or {}))

        if not self.s.gemini_model:
            raise LLMError("GEMINI_MODEL is not set. Run `python -m scripts.smoke_gemini` to list available models.")

        path = self.s.cache_dir / f"{self._key(task, prompt, schema, image)}.json"
        if path.exists():
            self.cache_hits += 1
            return schema.model_validate_json(path.read_text(encoding="utf-8"))

        from google.genai import types

        contents: list[Any] = [prompt]
        if image:
            contents.append(types.Part.from_bytes(data=image[0], mime_type=image[1]))
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, temperature=0.2
        )
        last: Exception | None = None
        for _ in range(2):
            try:
                resp = self._client_get().models.generate_content(
                    model=self.s.gemini_model, contents=contents, config=config
                )
                self.calls += 1
                parsed = resp.parsed if isinstance(getattr(resp, "parsed", None), schema) \
                    else schema.model_validate_json(resp.text)
                path.write_text(parsed.model_dump_json(), encoding="utf-8")
                return parsed
            except LLMError:
                raise
            except Exception as exc:  # network, quota, schema mismatch
                last = exc
        raise LLMError(f"Gemini call failed for task '{task}': {last}")
