# Token usage and cost report

Generated: 2026-09-13T07:53:21.095041+00:00 (run id 20260913T075319Z-a8cf19)
Dataset run: 250 requests (dataset/requests.csv). Provider: anthropic. Prompt version: v1.

Model calls are made only by the bounded evidence/explanation agents (two image readings per image, message extraction fallback with a verifier pass, explanation drafting with one feedback retry). All forecasting, plan generation, ranking and validation are deterministic Python. Cache hits make no API call and consume no tokens.

## Final full-dataset run: model work behind the shipped output.csv

Each shipped row is built from cached, provenance-tracked model outputs (image OCR, message extraction, explanation draft). A cached output is reused without a new API call, so this table sums the usage recorded when each output used by this run was originally produced. This is the per-request model cost of reproducing output.csv from scratch.

| Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---:|---:|---:|---:|---:|
| claude-sonnet-5 | 285 | 240138 | 31934 | 272072 | 0.7996 |
| **All models** | 285 | 240138 | 31934 | 272072 | 0.7996 |

- Total tokens: 272072; average per request: 1088.3
- Estimated total cost: USD 0.7996; average per request: USD 0.003198
- Calls per agent: {'explain_agent': 250, 'image_agent': 16, 'image_enumerate': 16, 'message_agent': 3}

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
