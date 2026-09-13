# Token usage and cost report

Generated: 2026-09-13T07:25:33.197762+00:00 (run id 20260913T072532Z-57c3f8)
Dataset run: 3 requests (dataset/requests.csv). Provider: anthropic. Prompt version: v1.

Model calls are made only by the bounded evidence/explanation agents (image OCR, message extraction fallback, explanation drafting). All forecasting, plan generation, ranking and validation are deterministic Python. Cache hits make no API call and consume no tokens.

## Final full-dataset run: model work behind the shipped output.csv

Each shipped row is built from cached, provenance-tracked model outputs (image OCR, message extraction, explanation draft). A cached output is reused without a new API call, so this table sums the usage recorded when each output used by this run was originally produced. This is the per-request model cost of reproducing output.csv from scratch.

| Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 38 | 74617 | 12591 | 87208 | 0.2751 |
| **All models** | 38 | 74617 | 12591 | 87208 | 0.2751 |

- Total tokens: 87208; average per request: 29069.3
- Estimated total cost: USD 0.2751; average per request: USD 0.091715
- Calls per agent: {'explain_agent': 3, 'image_agent': 16, 'image_enumerate': 16, 'message_agent': 3}

## API calls made during this run (cache misses only)

| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| **All models** | 0 | | 0 | 0 | 0 | 0.0000 |

- Total tokens: 0; average per request: 0.0
- Estimated total cost: USD 0.0000; average per request: USD 0.000000
- Calls per agent: {}

## Pricing assumptions (USD per million tokens)

- claude-sonnet-5: input 2.00, output 10.00
- claude-opus-5: input 5.00, output 25.00
- claude-haiku-4-5-20251001: input 1.00, output 5.00

No API keys or credentials are included in this package.
