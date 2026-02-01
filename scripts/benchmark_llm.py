"""Benchmark LLM providers for topic extraction.

Models tested:
- GPT-5 mini: $0.25/$2.00 per 1M tokens
- Claude Haiku 4.5: $1.00/$5.00 per 1M tokens
- DeepSeek V3.2: $0.28/$0.42 per 1M tokens
- Gemini 3 Flash: $0.50/$3.00 per 1M tokens
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import json
import statistics
from dataclasses import dataclass, field

from db import get_db, query_to_dicts

SAMPLE_SIZE = 20

PROMPT_TEMPLATE = """Extract 1-3 specific policy topics from this lobbying activity description.
Return ONLY a JSON array of topic strings. Topics should be specific and searchable (e.g., "tariffs", "drug pricing", "AI regulation", "electric vehicles", "Section 230").
If no specific topics can be identified, return an empty array.

Description: {description}

JSON array of topics:"""


@dataclass
class BenchmarkResult:
    provider: str
    model: str
    avg_latency_ms: float
    tokens_per_sec: float
    input_price: float  # per 1M tokens
    output_price: float  # per 1M tokens
    success_rate: float
    sample_outputs: list = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @property
    def estimated_cost_200k(self) -> float:
        """Estimate cost for 200K activities based on observed token usage."""
        if self.total_input_tokens == 0:
            # Fallback estimate
            avg_input = 200
            avg_output = 30
        else:
            avg_input = self.total_input_tokens / SAMPLE_SIZE
            avg_output = self.total_output_tokens / SAMPLE_SIZE

        input_cost = (avg_input * 200_000 * self.input_price) / 1_000_000
        output_cost = (avg_output * 200_000 * self.output_price) / 1_000_000
        return input_cost + output_cost


def get_sample_activities(n: int = SAMPLE_SIZE) -> list[dict]:
    """Get sample activities for benchmarking."""
    sql = """
        SELECT id, description FROM activities
        WHERE description IS NOT NULL AND LENGTH(description) > 50
        ORDER BY RANDOM()
        LIMIT ?
    """
    with get_db() as conn:
        return query_to_dicts(conn, sql, (n,))


def parse_topics(text: str) -> list:
    """Parse JSON array from LLM response."""
    text = text.strip()
    # Remove markdown code blocks
    if "```" in text:
        # Extract content between code blocks
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            # Remove language identifier and leading whitespace
            text = text.lstrip()
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
    text = text.strip()
    # Find the JSON array
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)


def benchmark_gpt5_mini(activities: list[dict]) -> BenchmarkResult:
    """Benchmark OpenAI GPT-5 mini."""
    import httpx

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("  OPENAI_API_KEY not set, skipping")
        return None

    client = httpx.Client(timeout=60)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    latencies = []
    outputs = []
    successes = 0
    total_input = 0
    total_output = 0

    for activity in activities:
        prompt = PROMPT_TEMPLATE.format(description=activity["description"][:1000])

        payload = {
            "model": "gpt-5-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 100
        }

        start = time.time()
        try:
            resp = client.post("https://api.openai.com/v1/chat/completions",
                             headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            elapsed = time.time() - start
            latencies.append(elapsed * 1000)

            text = data["choices"][0]["message"]["content"]
            topics = parse_topics(text)
            outputs.append({"id": activity["id"], "topics": topics})
            successes += 1

            total_input += data.get("usage", {}).get("prompt_tokens", 0)
            total_output += data.get("usage", {}).get("completion_tokens", 0)

        except Exception as e:
            print(f"    Error: {e}")
            latencies.append(5000)

        time.sleep(0.1)

    if not latencies or all(l >= 5000 for l in latencies):
        return None

    avg_latency = statistics.mean(latencies)
    tokens_per_sec = (total_output / len(activities)) / (avg_latency / 1000) if avg_latency > 0 else 0

    return BenchmarkResult(
        provider="OpenAI",
        model="gpt-5-mini",
        avg_latency_ms=avg_latency,
        tokens_per_sec=tokens_per_sec,
        input_price=0.25,
        output_price=2.00,
        success_rate=successes / len(activities),
        sample_outputs=outputs[:3],
        total_input_tokens=total_input,
        total_output_tokens=total_output
    )


def benchmark_haiku_45(activities: list[dict]) -> BenchmarkResult:
    """Benchmark Anthropic Claude Haiku 4.5."""
    import httpx

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ANTHROPIC_API_KEY not set, skipping")
        return None

    client = httpx.Client(timeout=60)
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    latencies = []
    outputs = []
    successes = 0
    total_input = 0
    total_output = 0

    for activity in activities:
        prompt = PROMPT_TEMPLATE.format(description=activity["description"][:1000])

        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": prompt}]
        }

        start = time.time()
        try:
            resp = client.post("https://api.anthropic.com/v1/messages",
                             headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            elapsed = time.time() - start
            latencies.append(elapsed * 1000)

            text = data["content"][0]["text"]
            topics = parse_topics(text)
            outputs.append({"id": activity["id"], "topics": topics})
            successes += 1

            total_input += data.get("usage", {}).get("input_tokens", 0)
            total_output += data.get("usage", {}).get("output_tokens", 0)

        except Exception as e:
            print(f"    Error: {e}")
            latencies.append(5000)

        time.sleep(0.1)

    if not latencies or all(l >= 5000 for l in latencies):
        return None

    avg_latency = statistics.mean(latencies)
    tokens_per_sec = (total_output / len(activities)) / (avg_latency / 1000) if avg_latency > 0 else 0

    return BenchmarkResult(
        provider="Anthropic",
        model="claude-haiku-4.5",
        avg_latency_ms=avg_latency,
        tokens_per_sec=tokens_per_sec,
        input_price=1.00,
        output_price=5.00,
        success_rate=successes / len(activities),
        sample_outputs=outputs[:3],
        total_input_tokens=total_input,
        total_output_tokens=total_output
    )


def benchmark_deepseek(activities: list[dict]) -> BenchmarkResult:
    """Benchmark DeepSeek V3.2."""
    import httpx

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("  DEEPSEEK_API_KEY not set, skipping")
        return None

    client = httpx.Client(timeout=60)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    latencies = []
    outputs = []
    successes = 0
    total_input = 0
    total_output = 0

    for activity in activities:
        prompt = PROMPT_TEMPLATE.format(description=activity["description"][:1000])

        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 100
        }

        start = time.time()
        try:
            resp = client.post("https://api.deepseek.com/chat/completions",
                             headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            elapsed = time.time() - start
            latencies.append(elapsed * 1000)

            text = data["choices"][0]["message"]["content"]
            topics = parse_topics(text)
            outputs.append({"id": activity["id"], "topics": topics})
            successes += 1

            total_input += data.get("usage", {}).get("prompt_tokens", 0)
            total_output += data.get("usage", {}).get("completion_tokens", 0)

        except Exception as e:
            print(f"    Error: {e}")
            latencies.append(5000)

        time.sleep(0.1)

    if not latencies or all(l >= 5000 for l in latencies):
        return None

    avg_latency = statistics.mean(latencies)
    tokens_per_sec = (total_output / len(activities)) / (avg_latency / 1000) if avg_latency > 0 else 0

    return BenchmarkResult(
        provider="DeepSeek",
        model="deepseek-chat (V3.2)",
        avg_latency_ms=avg_latency,
        tokens_per_sec=tokens_per_sec,
        input_price=0.28,
        output_price=0.42,
        success_rate=successes / len(activities),
        sample_outputs=outputs[:3],
        total_input_tokens=total_input,
        total_output_tokens=total_output
    )


def benchmark_gemini(activities: list[dict], model: str = "gemini-2.5-flash",
                     display_name: str = None, input_price: float = 0.15,
                     output_price: float = 0.60,
                     thinking_level: str = None) -> BenchmarkResult:
    """Benchmark Google Gemini models using the new google-genai SDK."""
    from google import genai
    from google.genai import types

    # Try both env var names
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("  GEMINI_API_KEY or GOOGLE_API_KEY not set, skipping")
        return None

    client = genai.Client(api_key=api_key)

    latencies = []
    outputs = []
    successes = 0
    total_input = 0
    total_output = 0

    # Build config for structured JSON output
    config_params = {
        "temperature": 0.1,
        "max_output_tokens": 256,
        "response_mime_type": "application/json",
    }

    # Add thinking config for Gemini 3 models
    if thinking_level and "gemini-3" in model:
        config_params["thinking_config"] = types.ThinkingConfig(
            thinking_level=thinking_level
        )

    config = types.GenerateContentConfig(**config_params)

    for activity in activities:
        prompt = PROMPT_TEMPLATE.format(description=activity["description"][:1000])

        start = time.time()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config
            )
            elapsed = time.time() - start
            latencies.append(elapsed * 1000)

            text = response.text
            if not text:
                raise ValueError("Empty response")
            topics = parse_topics(text)
            outputs.append({"id": activity["id"], "topics": topics})
            successes += 1

            # Get token counts from response
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                total_input += response.usage_metadata.prompt_token_count or 0
                total_output += response.usage_metadata.candidates_token_count or 0
            else:
                total_input += len(prompt) // 4
                total_output += len(text) // 4

        except json.JSONDecodeError as e:
            elapsed = time.time() - start
            latencies.append(elapsed * 1000)
        except Exception as e:
            print(f"    Error: {e}")
            latencies.append(5000)

        time.sleep(0.05)  # Light rate limiting

    if not latencies or all(l >= 5000 for l in latencies):
        return None

    avg_latency = statistics.mean([l for l in latencies if l < 5000]) if any(l < 5000 for l in latencies) else 5000
    tokens_per_sec = (total_output / max(successes, 1)) / (avg_latency / 1000) if avg_latency > 0 and successes > 0 else 0

    return BenchmarkResult(
        provider="Google",
        model=display_name or model,
        avg_latency_ms=avg_latency,
        tokens_per_sec=tokens_per_sec,
        input_price=input_price,
        output_price=output_price,
        success_rate=successes / len(activities),
        sample_outputs=outputs[:3],
        total_input_tokens=total_input,
        total_output_tokens=total_output
    )


def print_results(results: list[BenchmarkResult]):
    """Print benchmark results as a comparison table."""
    results = [r for r in results if r is not None]

    if not results:
        print("\nNo benchmarks completed. Set API keys and try again:")
        print("  export OPENAI_API_KEY=...")
        print("  export ANTHROPIC_API_KEY=...")
        print("  export DEEPSEEK_API_KEY=...")
        print("  export GOOGLE_API_KEY=...")
        return

    print("\n" + "="*90)
    print("BENCHMARK RESULTS")
    print("="*90)

    # Sort by estimated cost
    results = sorted(results, key=lambda x: x.estimated_cost_200k)

    # Header
    print(f"\n{'Provider':<12} {'Model':<20} {'Latency':<10} {'Tok/s':<8} {'Success':<8} {'Cost/200K':<10}")
    print("-"*90)

    for r in results:
        print(f"{r.provider:<12} {r.model:<20} {r.avg_latency_ms:>6.0f}ms   {r.tokens_per_sec:>6.1f}   {r.success_rate*100:>5.0f}%    ${r.estimated_cost_200k:>7.2f}")

    print("\n" + "="*90)
    print("SAMPLE OUTPUTS (showing quality)")
    print("="*90)

    for r in results:
        print(f"\n{r.provider} - {r.model}:")
        for sample in r.sample_outputs[:2]:
            topics = sample.get("topics", [])
            print(f"  → {topics}")

    print("\n" + "="*90)
    print("PRICING REFERENCE")
    print("="*90)
    print(f"\n{'Model':<25} {'Input $/1M':<12} {'Output $/1M':<12}")
    print("-"*50)
    for r in results:
        print(f"{r.model:<25} ${r.input_price:<10.2f} ${r.output_price:<10.2f}")


def main():
    print("Fetching sample activities...")
    activities = get_sample_activities(SAMPLE_SIZE)
    print(f"Got {len(activities)} activities for benchmarking\n")

    results = []

    print("Testing GPT-5 mini...")
    results.append(benchmark_gpt5_mini(activities))

    print("Testing Claude Haiku 4.5...")
    results.append(benchmark_haiku_45(activities))

    print("Testing DeepSeek V3.2...")
    results.append(benchmark_deepseek(activities))

    print("Testing Gemini 2.5 Flash...")
    results.append(benchmark_gemini(activities, "gemini-2.5-flash", "gemini-2.5-flash", 0.15, 0.60))

    print("Testing Gemini 3 Flash (minimal thinking)...")
    results.append(benchmark_gemini(activities, "gemini-3-flash-preview", "gemini-3-flash (minimal)", 0.50, 3.00, thinking_level="minimal"))

    print("Testing Gemini 3 Flash (low thinking)...")
    results.append(benchmark_gemini(activities, "gemini-3-flash-preview", "gemini-3-flash (low)", 0.50, 3.00, thinking_level="low"))

    print_results(results)


if __name__ == "__main__":
    main()
