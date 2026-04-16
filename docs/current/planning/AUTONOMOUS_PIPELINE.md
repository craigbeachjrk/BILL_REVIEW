# Autonomous Bill Parsing Pipeline — Implementation Plan

## Context

The bill parsing pipeline currently requires human review of every single bill before it can be posted to Entrata. A shadow agent already exists (AI review, garbage detection, knowledge base, autonomy framework) but has never been activated. The team also claims bills "go missing" in the pipeline with no way to trace them. Additionally, the parser sends raw PDF bytes to Gemini with no pre-processing, uses fragile pipe-separated output, and has no OCR fallback for scanned PDFs.

This plan delivers three interlocking capabilities across three phases:
- **Phase 1 (Visibility):** Pipeline queue tracker + autonomy simulation dashboard — zero risk to production
- **Phase 2 (Parser Overhaul):** Text extraction before LLM, JSON output, reference injection, confidence scoring
- **Phase 3 (Autonomy):** Activate assisted and autonomous modes based on proven accuracy

---

## PHASE 1: Visibility (Weeks 1-4) — No Production Parsing Risk

### 1A: Pipeline Queue Tracker

**Problem:** Bills "go missing" with no proof. No tracking from upload through every stage.

#### DynamoDB Table: `jrk-bill-pipeline-tracker`

```
pk (S): BILL#{s3_key_hash}          -- SHA1 of original S3 key (same as pdf_id)
sk (S): EVENT#{ISO_timestamp}       -- chronological sort

Attributes:
  event_type (S): UPLOADED | ROUTED | PARSING | PARSED | ENRICHING | ENRICHED |
                  REVIEW | SUBMITTED | POSTED | FAILED | REWORK
  s3_key (S): full S3 key at time of event
  stage (S): S1 | S1_Std | S1_Lg | S3 | S4 | S6 | S7 | S8 | S9
  source (S): lambda:jrk-bill-router | lambda:jrk-bill-parser | app:submit | user:{email}
  timestamp_epoch (N): epoch seconds
  filename (S): PDF filename for display
  metadata (S): JSON string (page_count, file_size_mb, vendor, error, etc.)
  ttl (N): epoch + 90 days (auto-expire)

GSI gsi-stage-time:
  pk: stage (S), sk: timestamp_epoch (N)
  → "Show all bills currently in Stage 4, sorted by arrival"

GSI gsi-date:
  pk: event_date (S) "2026-03-24", sk: pk (S)
  → "Show all bills that entered the system today"
```

#### Shared Helper Module: `pipeline_tracker.py`

Small module included in each Lambda deployment package:
```python
def track_event(ddb, table, s3_key, event_type, source, stage, metadata=None):
    # Fire-and-forget DDB put_item — never blocks bill processing
```

#### Lambda Instrumentation (5 Lambdas)

| Lambda | Events Written | Location |
|--------|---------------|----------|
| `jrk-bill-router` | ROUTED (with page_count, route decision) | After line 131 |
| `jrk-bill-parser` | PARSING (start), PARSED (success), FAILED (error) | Lines 794, 788, 911 |
| `jrk-bill-chunk-processor` | PARSING (chunk start), PARSED (chunk done) | Handler start/end |
| `jrk-bill-enricher` | ENRICHING (start), ENRICHED (done) | Lines 922, 938 |
| `main.py` (app) | SUBMITTED, POSTED, REWORK | Lines 25441, 2575, 23731 |

#### API Endpoints (main.py)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/pipeline/queue` | Real-time stage counts + oldest age (30s cache) |
| `GET /api/pipeline/bill/{pdf_id}` | Full lifecycle timeline for one bill |
| `GET /api/pipeline/stuck?threshold_minutes=60` | Bills stuck too long in any stage |
| `GET /api/pipeline/stats?hours=24` | Throughput by hour (uploaded/parsed/enriched/submitted) |

#### Frontend: `templates/pipeline.html`

- **Top:** Stage cards (count + oldest age, color: green <30min, yellow 30-60min, red >60min)
- **Middle:** Table of all in-flight bills (filename, current stage, time in stage, vendor)
- **Bottom:** Chart.js throughput over last 24 hours
- 30s auto-refresh, search by filename/pdf_id, click-to-expand lifecycle timeline
- Route: `GET /pipeline` (admin: full view, non-admin: filtered by property)

**Files to create/modify:**
- CREATE: `bill_review_app/aws_lambdas/shared/pipeline_tracker.py`
- CREATE: `templates/pipeline.html`
- MODIFY: All 5 Lambdas (add ~15 lines each)
- MODIFY: `main.py` (add 4 API endpoints + route, add event writes at submit/post/rework)
- AWS: Create DynamoDB table + 2 GSIs

---

### 1B: Autonomy Simulation Dashboard

**Problem:** Shadow agent exists but nobody knows if it's accurate. Need proof before enabling.

#### Key Decision: Deterministic-Only, Lambda-Computed

The simulation runs ONLY deterministic checks (no Gemini calls = free + fast + repeatable):
- `_detect_garbage_lines()` (main.py:214) — hardcoded + learned patterns
- `_get_account_history()` (main.py:363) — anomaly detection vs last 10 bills
- Knowledge base flag check — vendor-specific rules from DynamoDB
- Auto-pass logic (main.py:30678) — composite decision

A new Lambda runs this because AppRunner S3 GETs are 2-5s (loading 100 bills would take 200-500s on AppRunner vs 5-20s on Lambda).

#### New Lambda: `jrk-bill-autonomy-sim`

**Trigger:** CloudWatch Events daily at 2 AM UTC + on-demand via API.

**Logic:**
1. List recent Stage 4 + Stage 6 bills (configurable: last 7/14/30 days)
2. For each bill: load JSONL, run garbage detection, historical comparison, knowledge check, compute `would_auto_pass` + confidence
3. For submitted bills (Stage 6+): compare AI prediction to actual human actions (which lines deleted, GL changes)
4. Compute accuracy: TP/FP/FN for garbage, auto-pass correctness
5. Write results to S3: `Bill_Parser_Config/autonomy_sim_results.json.gz`

**Output schema:**
```json
{
  "computed_at": "2026-03-24T15:00:00Z",
  "date_range": {"start": "2026-03-17", "end": "2026-03-24"},
  "bills_analyzed": 347,
  "aggregate": {
    "would_auto_pass_pct": 72.3,
    "garbage_precision": 91.2,
    "garbage_recall": 85.7,
    "auto_pass_accuracy": 88.4
  },
  "by_vendor": [
    {"vendor_id": "123", "vendor_name": "SoCalEdison", "bills": 45,
     "would_auto_pass": 38, "accuracy": 95.6,
     "eligible_for_assisted": true, "eligible_for_autonomous": false}
  ],
  "recent_bills": [
    {"pdf_id": "abc123", "vendor_name": "SCE", "ai_decision": "auto_pass",
     "confidence": 92, "garbage_detected": 2, "garbage_correct": 2,
     "human_action": "submitted_unchanged", "ai_was_correct": true}
  ]
}
```

#### API Endpoints (main.py)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/autonomy/sim` | Cached simulation results (S3 → in-memory, `_metrics_serve` pattern) |
| `POST /api/autonomy/sim/run` | Trigger Lambda on-demand (admin only, async) |
| `GET /api/autonomy/sim/vendor/{vendor_id}` | Per-vendor drill-down |
| `GET /api/autonomy/sim/bill/{pdf_id}` | Real-time single-bill analysis (deterministic, fast) |

#### Frontend: `templates/autonomy_sim.html`

- **Header stats:** Total analyzed, auto-pass rate, garbage precision, overall accuracy
- **Vendor table:** Bill count, auto-pass %, accuracy %, eligibility badges (shadow/assisted/autonomous). Sortable + filterable by date range.
- **Expandable vendor rows:** Click to see individual bills
- **Per-bill detail modal:** Side-by-side — "AI would have done" vs "Human actually did" with color coding (green=correct, red=wrong)
- **Promotion controls:** For eligible vendors, "Promote to Assisted" button (calls existing `/api/autonomy/promote` at line 32020)
- Route: `GET /autonomy-sim` (admin only)

**Files to create/modify:**
- CREATE: New Lambda `jrk-bill-autonomy-sim/` with handler
- CREATE: `templates/autonomy_sim.html`
- MODIFY: `main.py` (add 4 API endpoints + route)
- AWS: CloudWatch Events rule for daily trigger

---

## PHASE 2: Parser Architecture Overhaul (Weeks 5-10)

### 2A: Text Extraction Layer

**Problem:** Raw PDF bytes sent to Gemini = expensive (image tokens), unreliable for scanned PDFs.

#### New Architecture
```
PDF arrives
    ↓
[1] pdfplumber: attempt text extraction per page
    ↓
    ├─ Text extracted (avg >50 chars/page) → DIGITAL PATH
    │     Send EXTRACTED TEXT to Gemini (10x fewer tokens, cheaper, faster)
    │
    └─ Sparse/no text → SCANNED PATH
          AWS Textract OCR → send OCR text to Gemini
          (also include raw PDF as image backup)
```

**Files to modify:**
- MODIFY: `lambda_bill_parser.py` — add `extract_text_from_pdf()`, `ocr_pdf_textract()`, modify `call_gemini_rest()` to support text-only path
- MODIFY: `lambda_chunk_processor.py` — same changes
- CREATE: Lambda Layer with `pdfplumber` + `Pillow` (~15MB, shared across parsers)

**Cost impact:** ~80% reduction in Gemini API cost for digital PDFs (70-80% of volume). Textract: ~$4.50/month for scanned PDFs.

---

### 2B: JSON Output Migration (Standard Parser)

**Problem:** Pipe-separated output causes 5-15% column-shift errors.

**Changes to `lambda_bill_parser.py`:**
1. Replace pipe-separated PROMPT (line 221) with JSON prompt matching chunk processor's format
2. Add `"responseMimeType": "application/json"` to Gemini config (forces JSON mode)
3. New `json_rows_to_columns()` converter replaces pipe parsing
4. Keep pipe-separated as fallback if JSON parse fails after 2 attempts
5. Eliminate `normalize_row()` for JSON path — no more column-shift repairs

**Downstream impact:** Zero. The `write_ndjson()` function (line 687) already converts rows to JSON dicts. Enricher sees identical JSONL format.

---

### 2C: Reference Data Injection

**Problem:** Parser has zero context about known vendors/properties. Gemini guesses blindly.

**Changes to `lambda_bill_parser.py` and `lambda_chunk_processor.py`:**
1. Build compact reference snippet (top 200 vendors + 200 properties, sorted alphabetically)
2. Append to parser prompt: "KNOWN VENDORS (use exact names when possible): ..."
3. Cap at ~4000 tokens of reference data

**Impact:** Vendor/property name accuracy should jump significantly. Enricher fuzzy-match calls may become unnecessary for many bills.

---

### 2D: Per-Field Confidence Scoring

**Depends on: 2B (JSON output)**

Add to JSON prompt:
```
For each object, include:
- confidence: {field_name: float 0.0-1.0} (only fields where confidence < 0.95)
```

Pass through as `field_confidence` in JSONL output. Simulation dashboard and review UI highlight low-confidence fields.

---

## PHASE 3: Actual Autonomy (Weeks 11-16)

### 3A: Enable Assisted Mode

**Prerequisite:** Phase 1B shows 80%+ accuracy for vendor.

**Changes:**
- `templates/review.html`: Add "AI Analysis" collapsible panel at top of review page. Shows confidence, garbage line badges, auto-pass recommendation.
- `main.py`: Auto-trigger deterministic analysis when loading a bill for review (if vendor is in assisted mode). Use existing `_get_autonomy_config()`.
- Reviewer sees AI suggestions pre-applied, can accept/reject with one click.

### 3B: Enable Autonomous Mode

**Prerequisite:** Phase 1B shows 95%+ accuracy over 20+ bills for vendor.

**New Lambda: `jrk-bill-auto-reviewer`**
- Triggered by S3 ObjectCreated on Stage 4
- Checks autonomy config for vendor → if autonomous + would_auto_pass + confidence ≥ 90: auto-advance to Stage 6
- Deletes garbage lines, writes AUTO_PASSED event to pipeline tracker
- If checks fail: leave in S4, write AUTO_FLAGGED event

**Safety rails:**
- Max 50 auto-passes/day/vendor (prevent runaway)
- Auto-demote if accuracy drops below 90% in last 7 days
- All auto-passed bills get "Robot" badge in UI for spot-checking
- Weekly accuracy report

### 3C: AI Billback Assignment (Future)

Not in immediate scope. Extend auto-reviewer to assign UBI periods once auto-review is proven.

---

## Dependency Graph

```
Phase 1A (Queue Tracker)     Phase 1B (Simulation Dashboard)    ← PARALLEL
        |                              |
        v                              v
Phase 2A (Text Extraction)   Phase 2B (JSON Output)   Phase 2C (Reference Injection)  ← PARALLEL
                                  |
                                  v
                        Phase 2D (Confidence Scoring)
                                  |
                                  v
                        Phase 3A (Assisted Mode)  ← needs 1B simulation data
                                  |
                                  v
                        Phase 3B (Autonomous Mode)
```

## Critical Files

| File | Phases | Changes |
|------|--------|---------|
| `main.py` | 1A, 1B, 3A | Pipeline tracker endpoints, simulation endpoints, assisted mode integration |
| `lambda_bill_parser.py` | 1A, 2A, 2B, 2C, 2D | Tracker instrumentation, text extraction, JSON output, reference injection, confidence |
| `lambda_chunk_processor.py` | 1A, 2A, 2C, 2D | Tracker instrumentation, text extraction, reference injection, confidence |
| `lambda_bill_enricher.py` | 1A | Tracker instrumentation |
| `lambda_bill_router.py` | 1A | Tracker instrumentation |
| `templates/pipeline.html` | 1A | NEW — queue tracker UI |
| `templates/autonomy_sim.html` | 1B | NEW — simulation dashboard |
| `templates/review.html` | 3A | AI analysis panel |
| `pipeline_tracker.py` | 1A | NEW — shared helper module |
| `jrk-bill-autonomy-sim/` | 1B | NEW — simulation Lambda |
| `jrk-bill-auto-reviewer/` | 3B | NEW — autonomous review Lambda |

## Existing Code to Reuse

| Function | Location | Reuse In |
|----------|----------|----------|
| `_detect_garbage_lines()` | main.py:214 | 1B Lambda, 3A, 3B |
| `_get_account_history()` | main.py:363 | 1B Lambda, 3A, 3B |
| `_get_autonomy_config()` | main.py:31872 | 3A, 3B |
| `_metrics_serve()` | main.py:~11575 | 1B API caching |
| `pdf_id_from_key()` | main.py:1646 | 1A tracker (same hash) |
| `load_day()` | main.py:1610 | 1B (reference for Lambda equivalent) |
| Chunk processor JSON prompt | lambda_chunk_processor.py:43 | 2B (template for standard parser) |

## Verification

**Phase 1A:** Upload a test PDF to S1. Verify events appear in tracker for ROUTED → PARSING → PARSED → ENRICHED. Check /pipeline page shows the bill.

**Phase 1B:** Run simulation Lambda against last 7 days. Verify /autonomy-sim shows per-vendor accuracy. Compare a few bills manually to confirm AI vs human comparison is correct.

**Phase 2:** Parse 50 bills with old (raw PDF + pipe) and new (text + JSON) paths. Compare accuracy. JSON path should eliminate column-shift errors entirely.

**Phase 3:** Enable assisted for one vendor. Verify AI panel appears on review page. Enable autonomous for one low-risk vendor. Verify bills auto-advance and accuracy holds.
