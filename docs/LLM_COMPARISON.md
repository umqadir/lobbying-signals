# LLM Options for Topic Extraction

**Task:** Extract 1-3 topics from ~200K lobbying activity descriptions (~100-500 tokens each)
**Hardware:** Apple M4 32GB

## Cloud Options (sorted by cost)

| Provider | Model | Input $/1M | Output $/1M | Speed (tok/s) | Notes |
|----------|-------|------------|-------------|---------------|-------|
| **DeepSeek** | V3.2 (cache hit) | $0.028 | $0.42 | ~100 | Cheapest with repeated prompts |
| **DeepSeek** | V3.2 (cache miss) | $0.28 | $0.42 | ~100 | Still very cheap |
| **Groq** | Llama 3.1 8B | $0.05 | $0.10 | 1,800 | Fastest inference, rate limited |
| **Cerebras** | Llama 3.1 8B | $0.10 | $0.10 | 1,800 | Fastest, pay-per-token |
| **Gemini** | 2.0 Flash | $0.10 | $0.40 | ~200 | Good balance |
| **Gemini** | 2.0 Flash-Lite | $0.07 | $0.30 | ~200 | Even cheaper |
| **GPT-4o-mini** | - | $0.15 | $0.60 | ~100 | Reliable, good quality |
| **Together** | Llama 3 8B | $0.20 | $0.20 | ~300 | Good model variety |
| **Fireworks** | Llama 3 8B | $0.20 | $0.20 | ~400 | Fast, 50% batch discount |
| **Claude** | 3.5 Haiku | $1.00 | $5.00 | ~100 | Expensive but high quality |

## Local Options (M4 32GB)

| Framework | Model | Tokens/sec | Memory | Notes |
|-----------|-------|------------|--------|-------|
| **MLX** | Llama 3.2 3B | ~100-150 | 3GB | Fastest framework for Mac |
| **MLX** | Qwen 2.5 7B | ~50-70 | 7GB | Good for extraction |
| **MLX** | Gemma 2 9B | ~40-50 | 9GB | Strong extraction |
| **Ollama** | Llama 3.2 3B | ~80-120 | 3GB | Easy to use |
| **Ollama** | Qwen 2.5 7B | ~35-50 | 7GB | 26-30% slower than MLX |
| **Ollama** | Phi-3 3.8B | ~60-80 | 4GB | Microsoft, good at tasks |

## Cost Estimate for 200K Activities

Assumptions:
- ~200 input tokens per request (prompt + description)
- ~30 output tokens per request (JSON array of topics)
- 200,000 requests total

| Provider | Input Cost | Output Cost | **Total** | Time at 100 tok/s |
|----------|-----------|-------------|-----------|-------------------|
| DeepSeek (cached) | $1.12 | $2.52 | **$3.64** | ~13 hours |
| Groq 8B | $2.00 | $0.60 | **$2.60** | ~1 hour |
| Cerebras 8B | $4.00 | $0.60 | **$4.60** | ~1 hour |
| Gemini Flash | $4.00 | $2.40 | **$6.40** | ~6 hours |
| GPT-4o-mini | $6.00 | $3.60 | **$9.60** | ~13 hours |
| Local (free) | $0 | $0 | **$0** | ~20-40 hours |

## Recommendations

### Best Value: **Groq or Cerebras**
- ~$2-5 total cost
- 1-2 hours to complete
- Simple API (OpenAI-compatible)

### Cheapest Cloud: **DeepSeek**
- ~$3-4 total with caching
- Slower, but excellent quality
- Cache mechanism great for repeated system prompts

### Free Option: **Local with MLX**
- Install: `pip install mlx-lm`
- Run: Qwen 2.5 7B or Llama 3.2 3B
- Takes 20-40 hours but $0 cost
- Can run overnight

### Highest Quality: **Claude 3.5 Haiku or GPT-4o-mini**
- ~$10-30 total
- Best for complex extraction
- Overkill for simple topic extraction

## Sources

- [LLM Price Check](https://llmpricecheck.com/)
- [DeepSeek Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [Groq Pricing](https://groq.com/pricing)
- [Cerebras Pricing](https://www.cerebras.ai/pricing)
- [Helicone LLM Cost Calculator](https://www.helicone.ai/llm-cost)
- [MLX Benchmarks](https://github.com/ggml-org/llama.cpp/discussions/4167)
- [Apple MLX Research](https://machinelearning.apple.com/research/exploring-llms-mlx-m5)
