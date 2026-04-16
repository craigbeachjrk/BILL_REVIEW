# S3: Pipeline Modularity & Chain Testing

## Problem
The current pipeline is a single monolithic path: email -> parse (Gemini) -> enrich -> review. There's no way to:
- Test a new parser without affecting production
- Compare accuracy between parsing approaches
- Swap in a new OCR engine or text extractor
- A/B test enrichment strategies
- Measure accuracy of each component independently

## Objective
Pipeline as composable chains. Each stage is a discrete component with defined inputs/outputs. Traffic can be routed to different chains for testing, comparison, and gradual rollout.

## Architecture Vision

```
                    +--> Chain A (V1): Gemini Flash -> Basic Enrich -> Auto-GL
Input (PDF/Email) --+--> Chain B (V2): Gemini Pro -> Enhanced Enrich -> ML-GL
                    +--> Chain C (V3): OCR + Textract -> Rule-based Parse -> Smart Enrich
```

Each chain component has:
- **Input contract**: `{pdf_bytes, metadata, config}`
- **Output contract**: `{parsed_records[], confidence, timing, errors[]}`
- **Accuracy metrics**: tracked per-chain, per-component

## Task Breakdown

### Phase 1: Component Abstraction
- [ ] **1.1** Define `ParseChainConfig` schema: `{chain_id, name, stages: [{stage_id, lambda_arn, config}]}`
- [ ] **1.2** Define stage contracts: `ParserInput`, `ParserOutput`, `EnricherInput`, `EnricherOutput`
- [ ] **1.3** Refactor current parser Lambda to conform to `ParserOutput` contract
- [ ] **1.4** Refactor current enricher Lambda to conform to `EnricherOutput` contract
- [ ] **1.5** Store chain configs in DynamoDB (`jrk-bill-config` PK=`CHAIN#chain_id`)

### Phase 2: Chain Router
- [ ] **2.1** Create `jrk-bill-chain-router` Lambda — reads chain config, routes to correct parser
- [ ] **2.2** Add routing rules: by property, by vendor, by percentage (A/B), by user
- [ ] **2.3** Tag parsed output with `chain_id` for downstream tracking
- [ ] **2.4** Add chain selection UI to admin config page

### Phase 3: Accuracy Comparison
- [ ] **3.1** Create accuracy tracking per chain: compare parsed output to human-reviewed final
- [ ] **3.2** Build accuracy dashboard: side-by-side chain comparison (precision, recall, field accuracy)
- [ ] **3.3** Add "shadow mode" — run chain B in parallel with chain A, compare results without affecting users
- [ ] **3.4** Auto-promote chain when accuracy exceeds threshold for N consecutive days

### Phase 4: New Chain Components
- [ ] **4.1** Build V2 parser: Gemini Pro with structured output + multi-pass validation
- [ ] **4.2** Build OCR pre-processor: AWS Textract -> text extraction -> LLM parsing
- [ ] **4.3** Build enhanced enricher: ML-based vendor/property matching
- [ ] **4.4** Build post-parse validator: automated field-level validation against known patterns

## Success Criteria
- New parser versions can be deployed and tested without touching production traffic
- Accuracy metrics visible per-chain within 24 hours of deployment
- Traffic can be shifted between chains via config change (no code deploy)
- Shadow mode allows risk-free testing of new chains
