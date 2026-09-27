"""LLM provider abstraction: IBM (primary) with Groq fallback."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from backend.config import settings

LOGGER = logging.getLogger("onboard.llm")


class LLMProvider:
    """Base LLM provider interface."""

    @property
    def enabled(self) -> bool:
        raise NotImplementedError

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str | None:
        raise NotImplementedError


class IBMProvider(LLMProvider):
    """IBM Bob / IBM-compatible OpenAI-format provider."""

    @property
    def enabled(self) -> bool:
        return bool(
            settings.ibm_api_key
            and settings.ibm_base_url
            and settings.ibm_model
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str | None:

        if not self.enabled:
            return None

        payload = json.dumps(
            {
                "model": settings.ibm_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")

        url = (
            f"{settings.ibm_base_url.rstrip('/')}"
            "/chat/completions"
        )

        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.ibm_api_key}",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=120,
            ) as response:

                body = json.loads(
                    response.read(2_000_000).decode("utf-8")
                )

            content = body["choices"][0]["message"]["content"]

            if not isinstance(content, str):
                LOGGER.error(
                    "IBM returned non-string content"
                )
                return None

            return content.strip()

        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            LOGGER.error(
                "IBM LLM request failed status=%s reason=%s body=%s",
                exc.code,
                exc.reason,
                error_body[:2000],
            )

            return None

        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            urllib.error.URLError,
        ) as exc:

            LOGGER.error(
                "IBM LLM request failed error=%s",
                type(exc).__name__,
            )

            return None


class GroqProvider(LLMProvider):
    """Groq LLM provider using the official Groq Python SDK."""

    def __init__(self) -> None:
        self.client = None

        if settings.groq_api_key:
            try:
                from groq import Groq

                self.client = Groq(
                    api_key=settings.groq_api_key,
                )

                LOGGER.info(
                    "Groq client initialized successfully"
                )

            except Exception as exc:
                LOGGER.error(
                    "Failed to initialize Groq client "
                    "error=%s message=%s",
                    type(exc).__name__,
                    str(exc)[:1000],
                )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str | None:

        if not self.enabled:
            return None

        try:
            response = self.client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content

            if not isinstance(content, str):
                LOGGER.error(
                    "Groq returned non-string content"
                )
                return None

            return content.strip()

        except Exception as exc:
            LOGGER.error(
                "Groq LLM request failed "
                "error=%s message=%s",
                type(exc).__name__,
                str(exc)[:1000],
            )

            return None


class FallbackLLM:
    """Try IBM first, then fall back to Groq."""

    def __init__(self) -> None:
        self.ibm = IBMProvider()
        self.groq = GroqProvider()

    @property
    def enabled(self) -> bool:
        return self.ibm.enabled or self.groq.enabled

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str | None:

        # Primary provider: IBM Bob
        if self.ibm.enabled:
            result = self.ibm.complete(
                messages,
                temperature,
                max_tokens,
            )

            if result:
                return result

            LOGGER.warning(
                "IBM LLM failed, trying Groq fallback"
            )

        # Fallback provider: Groq
        if self.groq.enabled:
            result = self.groq.complete(
                messages,
                temperature,
                max_tokens,
            )

            if result:
                return result

            LOGGER.error(
                "Groq LLM fallback also failed"
            )

        LOGGER.error(
            "All configured LLM providers failed"
        )

        return None