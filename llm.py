"""LLM abstraction layer with swappable providers."""

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from config import LLM_PROVIDER, LLM_MODEL, GEMINI_API_KEY, ALL_ISSUE_LABELS


class BaseLLM(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def classify(self, text: str, labels: list[str]) -> tuple[str, float]:
        """Classify text into one of the given labels.
        Returns (label, confidence)."""
        pass

    @abstractmethod
    def extract_issues(self, description: str) -> list[dict]:
        """Extract issue labels from a lobbying activity description.
        Returns list of {"label": str, "confidence": float}."""
        pass

    @abstractmethod
    def generate_narrative(self, signal: dict, context: dict) -> str:
        """Generate a publishable narrative for a signal."""
        pass


class GeminiLLM(BaseLLM):
    """Gemini implementation."""

    def __init__(self, model: str = None, api_key: str = None):
        import google.generativeai as genai

        self.model_name = model or LLM_MODEL
        api_key = api_key or GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY environment variable required")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(self.model_name)

    def classify(self, text: str, labels: list[str]) -> tuple[str, float]:
        prompt = f"""Classify the following text into exactly one of these categories: {', '.join(labels)}

Text: {text}

Respond with JSON only: {{"label": "category_name", "confidence": 0.0-1.0}}"""

        response = self.model.generate_content(prompt)
        try:
            result = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
            return result["label"], result["confidence"]
        except (json.JSONDecodeError, KeyError):
            return labels[0], 0.5

    def extract_issues(self, description: str) -> list[dict]:
        labels_str = ", ".join(ALL_ISSUE_LABELS)
        prompt = f"""Analyze this lobbying activity description and identify which specific issues it relates to.

Description: {description}

Available issue labels: {labels_str}

Return a JSON array of the most relevant issues (1-3 maximum):
[{{"label": "issue_name", "confidence": 0.0-1.0}}]

Only include issues that are clearly relevant. If none match well, return an empty array."""

        response = self.model.generate_content(prompt)
        try:
            text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(text)
        except (json.JSONDecodeError, KeyError):
            return []

    def generate_narrative(self, signal: dict, context: dict) -> str:
        prompt = f"""Generate a publishable news brief about this lobbying data anomaly.

Signal Details:
- Type: {signal.get('signal_type')}
- Entity: {signal.get('entity_name')} ({signal.get('entity_type')})
- Metric: {signal.get('metric')}
- Current Value: ${signal.get('current_value', 0):,.0f}
- Prior Value: ${signal.get('prior_value', 0):,.0f}
- Growth Rate: {signal.get('growth_rate', 0) * 100:.1f}%
- Historical Percentile: {signal.get('historical_pct', 0) * 100:.0f}th percentile
- Period: Q{signal.get('quarter')} {signal.get('year')}

Additional Context:
{json.dumps(context, indent=2)}

Write a concise, factual news brief in this format:

HEADLINE: [One compelling sentence summarizing the story]

CONTEXT: [2-3 sentences explaining the historical baseline and why this is unusual]

KEY PLAYERS: [Bullet list of top firms or clients involved]

LIKELY DRIVER: [1-2 sentences on probable cause - recent legislation, executive action, etc.]

DATA NOTE: [Brief sourcing statement about LDA filings]"""

        response = self.model.generate_content(prompt)
        return response.text.strip()


class OpenAILLM(BaseLLM):
    """OpenAI implementation (placeholder for easy swapping)."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: str = None):
        import openai

        self.model_name = model
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable required")

        self.client = openai.OpenAI(api_key=api_key)

    def _chat(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content

    def classify(self, text: str, labels: list[str]) -> tuple[str, float]:
        prompt = f"""Classify the following text into exactly one of these categories: {', '.join(labels)}

Text: {text}

Respond with JSON only: {{"label": "category_name", "confidence": 0.0-1.0}}"""

        response = self._chat(prompt)
        try:
            result = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
            return result["label"], result["confidence"]
        except (json.JSONDecodeError, KeyError):
            return labels[0], 0.5

    def extract_issues(self, description: str) -> list[dict]:
        labels_str = ", ".join(ALL_ISSUE_LABELS)
        prompt = f"""Analyze this lobbying activity description and identify which specific issues it relates to.

Description: {description}

Available issue labels: {labels_str}

Return a JSON array of the most relevant issues (1-3 maximum):
[{{"label": "issue_name", "confidence": 0.0-1.0}}]

Only include issues that are clearly relevant. If none match well, return an empty array."""

        response = self._chat(prompt)
        try:
            text = response.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(text)
        except (json.JSONDecodeError, KeyError):
            return []

    def generate_narrative(self, signal: dict, context: dict) -> str:
        prompt = f"""Generate a publishable news brief about this lobbying data anomaly.

Signal Details:
- Type: {signal.get('signal_type')}
- Entity: {signal.get('entity_name')} ({signal.get('entity_type')})
- Metric: {signal.get('metric')}
- Current Value: ${signal.get('current_value', 0):,.0f}
- Prior Value: ${signal.get('prior_value', 0):,.0f}
- Growth Rate: {signal.get('growth_rate', 0) * 100:.1f}%
- Historical Percentile: {signal.get('historical_pct', 0) * 100:.0f}th percentile
- Period: Q{signal.get('quarter')} {signal.get('year')}

Additional Context:
{json.dumps(context, indent=2)}

Write a concise, factual news brief in this format:

HEADLINE: [One compelling sentence summarizing the story]

CONTEXT: [2-3 sentences explaining the historical baseline and why this is unusual]

KEY PLAYERS: [Bullet list of top firms or clients involved]

LIKELY DRIVER: [1-2 sentences on probable cause - recent legislation, executive action, etc.]

DATA NOTE: [Brief sourcing statement about LDA filings]"""

        return self._chat(prompt)


def get_llm(provider: str = None, model: str = None) -> BaseLLM:
    """Factory function to get the configured LLM."""
    provider = provider or LLM_PROVIDER

    if provider == "gemini":
        return GeminiLLM(model=model)
    elif provider == "openai":
        return OpenAILLM(model=model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
