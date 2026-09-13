# Token usage and cost report

Generated: 2026-09-13T03:11:25.637188+00:00
Dataset run: 250 requests (dataset/requests.csv). Provider: anthropic. Prompt version: v1.

Model calls are made only by the bounded evidence/explanation agents (image OCR, message extraction fallback, explanation drafting, optional audit). All forecasting, plan generation, ranking and validation are deterministic Python. Cache hits make no API call and consume no tokens.

## Final full-dataset run: model work behind the shipped output.csv

Each shipped row is built from cached, provenance-tracked model outputs (image OCR, message extraction, explanation draft). A cached output is reused without a new API call, so this table sums the usage recorded when each output used by this run was originally produced. This is the per-request model cost of reproducing output.csv from scratch.

| Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 269 | 178577 | 23498 | 202075 | 0.5921 |
| **All models** | 269 | 178577 | 23498 | 202075 | 0.5921 |

- Total tokens: 202075; average per request: 808.3
- Estimated total cost: USD 0.5921; average per request: USD 0.002369
- Calls per agent: {'explain_agent': 250, 'image_agent': 16, 'message_agent': 3}

## API calls made during this run (cache misses only)

| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| **All models** | 0 | | 0 | 0 | 0 | 0.0000 |

- Total tokens: 0; average per request: 0.0
- Estimated total cost: USD 0.0000; average per request: USD 0.000000
- Calls per agent: {}

## Cumulative calls that built the shipped evidence caches (all runs)

| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 317 | 6 | 203619 | 27134 | 230753 | 0.6786 |
| **All models** | 317 | | 203619 | 27134 | 230753 | 0.6786 |

- Cumulative tokens: 230753; per request: 923.0; cumulative cost: USD 0.6786 (USD 0.002714 per request)

## Pricing assumptions (USD per million tokens)

- claude-sonnet-5: input 2.00, output 10.00
- claude-opus-5: input 5.00, output 25.00
- claude-haiku-4-5-20251001: input 1.00, output 5.00

No API keys or credentials are included in this package.
