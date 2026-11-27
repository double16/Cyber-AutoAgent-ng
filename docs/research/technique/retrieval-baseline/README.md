# Technique retrieval baseline

Provider: Ollama; embedding model: `mxbai-embed-large:latest` (1,024 dimensions).
The report contains the run timestamp and exact catalog/case fingerprints.

Catalog: 65 module techniques. Cases: seven positive requests and one catalog miss.
Labels were authored from module guidance for this implementation; they have not had independent review.
This is an exploratory regression baseline, not evidence of general embedding quality.

| Representation | Limit | Mean recall | Mean reciprocal rank | Mean retrieval seconds |
| --- | --- | --- | --- | --- |
| full | 10 | 1.000 | 0.929 | 0.059 |
| full | 20 | 1.000 | 0.929 | 0.095 |
| summary | 10 | 1.000 | 0.929 | 0.057 |
| summary | 20 | 1.000 | 0.929 | 0.098 |

All seven labeled techniques appeared within the first ten candidates in both modes.
The SSRF request ranked its generic technique second; the other positive cases ranked first.
The unlabeled physical-lock request still retrieves neighbors. Similarity cannot establish applicability;
semantic review must reject irrelevant candidates.

Keep full-document embeddings and the current shortlist defaults. The small, ordered run is insufficient
to establish a latency advantage or calibrate similarity cutoffs. The 300-distractor tests separately
exercise catalog scaling with controlled vectors; this live catalog contains 65 documents.

This run measured retrieval only. Actual workflow precision and unnecessary-generation metrics are
implemented and covered by scripted tests, but have not been measured with the production LLM.
No target requests or operation continuations were executed.

Artifacts: [raw results](results.json),
[case labels](../../../../tests/fixtures/techniques/retrieval_cases.json).
