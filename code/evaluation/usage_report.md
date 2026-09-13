# Token usage and cost report

Generated: 2026-09-13T00:52:53.933998+00:00
Dataset run: 3 requests (dataset/requests.csv). Provider: anthropic. Prompt version: v1.

Model calls are made only by the bounded evidence/explanation agents (image OCR, message extraction fallback, explanation drafting, optional audit). All forecasting, plan generation, ranking and validation are deterministic Python. Cache hits make no API call and consume no tokens.

## Final full-dataset run (calls made during this run)

| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 3 | 3 | 0 | 0 | 0 | 0.0000 |
| **All models** | 3 | | 0 | 0 | 0 | 0.0000 |

- Total tokens: 0; average per request: 0.0
- Estimated total cost: USD 0.0000; average per request: USD 0.000000
- Calls per agent: {'image_agent': 3}

## Cumulative calls that built the shipped evidence caches (all runs)

| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 298 | 6 | 189717 | 25189 | 214906 | 0.6313 |
| **All models** | 298 | | 189717 | 25189 | 214906 | 0.6313 |

- Cumulative tokens: 214906; per request: 71635.3; cumulative cost: USD 0.6313 (USD 0.210441 per request)

## Pricing assumptions (USD per million tokens)

- claude-sonnet-5: input 2.00, output 10.00
- claude-opus-5: input 5.00, output 25.00
- claude-haiku-4-5-20251001: input 1.00, output 5.00

No API keys or credentials are included in this package.
