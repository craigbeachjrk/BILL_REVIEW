# AI-Native Bill Review Platform - Implementation Plan

## Executive Summary

Transform the bill review platform from a human-driven workflow with AI assistance to an **AI-native platform** where AI does the work and humans provide oversight. The end state: AI parses, AI reviews, AI assigns billbacks, AI answers questions - humans approve and handle exceptions.

---

## Phase 1: Chunk-to-Line Mapping ✅ COMPLETE

**Commit:** `bda6e79` (2026-02-12)
**Status:** Implemented, pushed, awaiting deployment

### What Was Implemented

#### Lambda Changes

1. **jrk-bill-large-parser**
   - Added `pages_per_chunk` parameter to `create_job_record()`
   - Stores `PAGES_PER_CHUNK` env var (default: 2) in DynamoDB job record

2. **jrk-bill-chunk-processor**
   - Reads `pages_per_chunk` from job_info (defaults to 2)
   - Calculates page range: `source_page_start = (chunk_num - 1) * pages_per_chunk + 1`
   - Adds to result_data: `pages_per_chunk`, `source_page_start`, `source_page_end`

3. **jrk-bill-aggregator**
   - `combine_chunk_results()` now returns list of dicts with page metadata
   - Each row wrapped as: `{"row": [...], "chunk_num": N, "source_page_start": N, "source_page_end": N}`
   - `write_final_jsonl()` adds `source_chunk`, `source_page_start`, `source_page_end` to output

4. **jrk-bill-parser** (standard)
   - Optional PyPDF2 import with graceful degradation
   - `count_pdf_pages()` function returns page count (0 if PyPDF2 unavailable)
   - Adds to output: `source_chunk=0`, `source_page_start=1`, `source_page_end=total_pages`

#### Application Changes

5. **main.py** (`/review` endpoint)
   - Reads `source_page_start`, `source_page_end`, `source_chunk` from row data
   - Passes to template (defaults to 0 if missing)

6. **templates/review.html**
   - CSS for `.page-badge` and `.page-badge.no-page` states
   - Page badges next to line numbers (e.g., "📄 p1-2")
   - `jumpToPage(pageNum)` function navigates PDF via `#page=N` fragment
   - `addLine()` and `duplicateSelectedLines()` reset page badges for new lines

#### New JSONL Fields

| Field | Type | Description |
|-------|------|-------------|
| `source_chunk` | int | Chunk number (0 = standard parser, 1+ = chunked) |
| `source_page_start` | int | First page this line came from |
| `source_page_end` | int | Last page this line came from |

#### Backward Compatibility

- Old bills without page metadata show "—" badge
- Missing fields default to 0
- PyPDF2 gracefully degrades if not in Lambda package
- Enricher passes through new fields unchanged

### Deployment Checklist

- [ ] Deploy `jrk-bill-large-parser` Lambda
- [ ] Deploy `jrk-bill-chunk-processor` Lambda
- [ ] Deploy `jrk-bill-aggregator` Lambda
- [ ] Deploy `jrk-bill-parser` Lambda
- [ ] Deploy app via `deploy_app.ps1`
- [ ] Test with new multi-page bill

---

## Phase 1 (Original Plan - For Reference)

### Goal
Users can see exactly which PDF page produced which line items, making verification faster and more intuitive.

### 1.1 Add Page Metadata to Chunk Processor

**File:** `aws_lambdas/us-east-1/jrk-bill-chunk-processor/code/lambda_chunk_processor.py`

**Changes:**
```python
# In parse_chunk_with_retry(), after extracting rows (around line 554-613)
# Add to each row:
row_data["source_chunk_id"] = chunk_id           # e.g., "chunk_003"
row_data["source_page_start"] = (chunk_num - 1) * PAGES_PER_CHUNK + 1  # e.g., 5
row_data["source_page_end"] = chunk_num * PAGES_PER_CHUNK              # e.g., 6
row_data["source_page_range"] = f"{page_start}-{page_end}"             # e.g., "5-6"
```

**New fields added to JSONL:**
| Field | Example | Purpose |
|-------|---------|---------|
| `source_chunk_id` | `"chunk_003"` | Which chunk produced this line |
| `source_page_start` | `5` | First page of chunk in original PDF |
| `source_page_end` | `6` | Last page of chunk |
| `source_page_range` | `"5-6"` | Display string |

### 1.2 Preserve Page Metadata in Aggregator

**File:** `aws_lambdas/us-east-1/jrk-bill-aggregator/code/lambda_aggregator.py`

**Changes:**
- In `combine_chunk_results()`: Don't strip page metadata fields
- Pass through `source_chunk_id`, `source_page_start`, `source_page_end` unchanged

### 1.3 Add Page Metadata to Standard Parser

**File:** `aws_lambdas/us-east-1/jrk-bill-parser/code/lambda_bill_parser.py`

**Changes:**
```python
# For single-page or small bills, add page tracking
# In write_ndjson() around line 670:
if total_pages == 1:
    data["source_page_range"] = "1"
else:
    # For multi-line bills from single parse, estimate page from line position
    data["source_page_range"] = "1-" + str(total_pages)
    data["source_page_estimate"] = True  # Flag that this is estimated
```

### 1.4 JSON Output Standardization

**Current State:**
- Standard parser: Pipe-separated → converted to JSON
- Chunk processor: Native JSON output

**Change:** Modify standard parser to output JSON directly (like chunk processor)

**File:** `aws_lambdas/us-east-1/jrk-bill-parser/code/lambda_bill_parser.py`

**Changes:**
```python
# Replace COLUMNS pipe-separated format with JSON schema
# Change prompt from:
#   "Return exactly 30 pipe-separated columns..."
# To:
#   "Return a JSON array of objects with these fields..."

OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "bill_to_name": {"type": "string"},
            "vendor_name": {"type": "string"},
            "account_number": {"type": "string"},
            "service_address": {"type": "string"},
            # ... all 30 fields
            "line_item_description": {"type": "string"},
            "line_item_charge": {"type": "number"}
        }
    }
}
```

**Benefits:**
- Cleaner prompt (JSON is more natural for LLMs)
- Easier validation (JSON schema vs regex parsing)
- Consistent with chunk processor
- Better error messages from Gemini

### 1.5 UI: Page-Aware Line Item Display

**File:** `templates/review.html`

**Changes:**

1. **Page badge on each line:**
```html
<div class="line-item">
  <span class="page-badge" onclick="jumpToPage(${line.source_page_start})">
    Page ${line.source_page_range}
  </span>
  <span class="description">${line.description}</span>
  <span class="amount">${line.charge}</span>
</div>
```

2. **Jump to page in PDF viewer:**
```javascript
function jumpToPage(pageNum) {
  const pdfFrame = document.getElementById('pdfFrame');
  // PDF.js or iframe with #page=N
  pdfFrame.src = pdfUrl + '#page=' + pageNum;
}
```

3. **Color-code by chunk:**
```css
.line-item[data-chunk="1"] { border-left: 3px solid #3b82f6; }
.line-item[data-chunk="2"] { border-left: 3px solid #10b981; }
.line-item[data-chunk="3"] { border-left: 3px solid #f59e0b; }
/* ... rotating colors */
```

4. **Chunk summary header:**
```html
<div class="chunk-summary">
  <span>Chunk 1 (Pages 1-2): 4 lines</span>
  <span>Chunk 2 (Pages 3-4): 3 lines</span>
  <span>Chunk 3 (Pages 5-6): 5 lines</span>
</div>
```

### 1.6 Deployment Steps

1. Deploy updated `jrk-bill-chunk-processor` Lambda
2. Deploy updated `jrk-bill-aggregator` Lambda
3. Deploy updated `jrk-bill-parser` Lambda (JSON output)
4. Update `jrk-bill-enricher` to handle new JSON format
5. Deploy frontend changes to `review.html`
6. Test with sample multi-page bill

---

## Phase 1.5: AP Knowledge Base ✅ COMPLETE

**Commit:** `98c43e2` (2026-02-12)
**Status:** Implemented, awaiting DynamoDB table creation and deployment

### What Was Implemented

1. **DynamoDB Table Design** - `jrk-bill-knowledge-base`
   - pk: `{ENTITY_TYPE}#{entity_id}` (e.g., `VENDOR#sce`, `PROPERTY#main_st`)
   - sk: `NOTE#{timestamp}#{author}`

2. **API Endpoints** (main.py)
   - `GET /api/knowledge` - List/search notes (filter by entity_type, category, search)
   - `POST /api/knowledge` - Add new knowledge note
   - `PUT /api/knowledge/{entity_type}/{entity_id}` - Update note
   - `POST /api/knowledge/{entity_type}/{entity_id}/verify` - Verify note from another AP
   - `DELETE /api/knowledge/{entity_type}/{entity_id}` - Delete note (author only)
   - `GET /api/knowledge/for-invoice` - Get notes relevant to current invoice

3. **Standalone UI** (`templates/knowledge_base.html`)
   - Full CRUD interface for managing knowledge notes
   - Filter by entity type, category, search term
   - Statistics dashboard (total notes, vendors, properties, contributors)
   - Verification system - APs can verify others' notes

4. **Review Page Integration** (`templates/review.html`)
   - Collapsible "Knowledge Notes" panel between header and line items
   - Loads notes for current vendor/property automatically
   - Shows count badge when notes exist
   - Links to full knowledge base

### Deployment Checklist

- [ ] Create DynamoDB table `jrk-bill-knowledge-base`:
  ```bash
  aws dynamodb create-table \
    --table-name jrk-bill-knowledge-base \
    --attribute-definitions \
      AttributeName=pk,AttributeType=S \
      AttributeName=sk,AttributeType=S \
    --key-schema \
      AttributeName=pk,KeyType=HASH \
      AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region us-east-1
  ```
- [ ] Deploy app via `deploy_app.ps1`
- [ ] Add initial knowledge notes for common vendors

---

## Phase 1.5 (Original Plan - For Reference)

### Goal
APs document their institutional knowledge about vendors and properties. This knowledge feeds the AI review agent, making it smarter over time.

### 1.5.1 Knowledge Base Data Model

**DynamoDB Table:** `jrk-bill-knowledge-base`

```json
{
  "pk": "VENDOR#sce",  // or "PROPERTY#main_st_complex" or "ACCOUNT#prop|vendor|acct"
  "sk": "NOTE#2026-02-13T10:00:00Z#user@jrk.com",
  "entity_type": "vendor",  // vendor, property, account
  "entity_id": "sce",
  "entity_name": "Southern California Edison",
  "category": "billing_pattern",  // billing_pattern, common_issues, expected_values, special_rules
  "content": "SCE bills monthly around the 15th. Typical residential is $80-150. Watch for 'minimum charge' line item on vacant units.",
  "author": "msmith@jrk.com",
  "created_at": "2026-02-13T10:00:00Z",
  "updated_at": "2026-02-13T10:00:00Z",
  "tags": ["billing_cycle", "vacant_units", "expected_amount"],
  "applies_to_properties": ["*"],  // or specific property IDs
  "confidence": "high",  // high, medium, low - how certain is this knowledge
  "verified_by": ["jdoe@jrk.com"],  // other APs who confirmed this
  "last_verified": "2026-02-13"
}
```

### 1.5.2 Knowledge Categories

| Category | Example Notes | Used For |
|----------|---------------|----------|
| **billing_pattern** | "Bills monthly, usually 28-32 day cycles" | Detecting unusual bill frequency |
| **expected_values** | "Typical range $500-800 for this property" | Flagging anomalous amounts |
| **common_issues** | "Often missing meter number, pull from account #" | Auto-fixing known gaps |
| **special_rules** | "This vendor uses quarterly billing Oct-Dec" | Multi-period assignment |
| **gl_mapping** | "Stormwater charges go to 6140, not 6110" | Correct GL assignment |
| **contacts** | "Call 1-800-xxx for account disputes" | Reference info |
| **gotchas** | "They changed account format in 2025, old bills use different #" | Parser hints |

### 1.5.3 Knowledge Entry UI

**New File:** `templates/knowledge_base.html`

**Features:**
- Browse by vendor, property, or account
- Add/edit notes with rich text
- Tag notes for categorization
- See all notes from other APs
- Verify/confirm others' notes
- Link to example invoices that demonstrate the knowledge

**Entry Form:**
```
┌─────────────────────────────────────────────────────────────┐
│ Add Knowledge Note                                          │
├─────────────────────────────────────────────────────────────┤
│ Entity Type: [Vendor ▼]                                     │
│ Entity:      [Southern California Edison    ] (searchable)  │
│                                                             │
│ Category:    [Expected Values ▼]                            │
│                                                             │
│ Note:                                                       │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Typical monthly bill ranges:                            │ │
│ │ - Residential units: $80-150                            │ │
│ │ - Common areas: $200-400                                │ │
│ │ - Vacant units: $15-30 (minimum charge only)            │ │
│ │                                                         │ │
│ │ Red flags:                                              │ │
│ │ - Bill > $500 for single unit = investigate            │ │
│ │ - Negative amounts = credit, verify with property mgr   │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ Tags: [expected_amount] [residential] [vacant] [+Add]       │
│                                                             │
│ Applies to: ○ All properties  ● Specific: [Main St Complex] │
│                                                             │
│ Confidence: [High ▼]                                        │
│                                                             │
│ Link example invoice: [Browse...] (optional)                │
│                                                             │
│                              [Cancel]  [Save Knowledge]     │
└─────────────────────────────────────────────────────────────┘
```

### 1.5.4 Knowledge Display in Review UI

**File:** `templates/review.html`

**Enhancement:** Show relevant knowledge while reviewing an invoice

```
┌─────────────────────────────────────────────────────────────┐
│ 💡 AP Knowledge for Southern California Edison              │
├─────────────────────────────────────────────────────────────┤
│ 📊 Expected Values (by msmith, verified by 2 others)        │
│    Typical range: $80-150 residential, $200-400 common      │
│                                                             │
│ ⚠️ Common Issues (by jdoe)                                  │
│    Often missing meter number - check account number        │
│                                                             │
│ 📅 Billing Pattern (by msmith)                              │
│    Monthly billing, arrives around 15th                     │
│                                                             │
│ [View all 5 notes] [Add note for this vendor]               │
└─────────────────────────────────────────────────────────────┘
```

### 1.5.5 Knowledge API Endpoints

**File:** `main.py`

```python
@app.get("/api/knowledge")
def get_knowledge(
    entity_type: str = None,  # vendor, property, account
    entity_id: str = None,
    category: str = None,
    search: str = None,
    user: str = Depends(require_user)
):
    """Get knowledge notes, filtered by entity or search."""
    pass

@app.post("/api/knowledge")
def add_knowledge(
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    entity_name: str = Form(...),
    category: str = Form(...),
    content: str = Form(...),
    tags: str = Form(""),  # comma-separated
    applies_to: str = Form("*"),
    confidence: str = Form("medium"),
    user: str = Depends(require_user)
):
    """Add a knowledge note."""
    pass

@app.post("/api/knowledge/{note_id}/verify")
def verify_knowledge(note_id: str, user: str = Depends(require_user)):
    """Mark a knowledge note as verified by current user."""
    pass

@app.get("/api/knowledge/for-invoice/{pdf_id}")
def get_knowledge_for_invoice(pdf_id: str, user: str = Depends(require_user)):
    """Get all relevant knowledge for a specific invoice (vendor + property + account)."""
    pass
```

### 1.5.6 Historical Invoice Comparison

**Stored with each invoice in Stage 4+:**
```json
{
  "historical_summary": {
    "account_key": "prop|vendor|acct",
    "invoice_count": 24,
    "avg_amount": 125.50,
    "min_amount": 85.00,
    "max_amount": 180.00,
    "std_dev": 22.30,
    "typical_line_count": 3,
    "last_3_bills": [
      {"date": "01/15/2026", "amount": 130.00, "lines": 3},
      {"date": "12/15/2025", "amount": 125.00, "lines": 3},
      {"date": "11/15/2025", "amount": 118.00, "lines": 3}
    ],
    "billing_frequency_days": 30,
    "anomaly_score": 0.12  // 0 = normal, 1 = very unusual
  }
}
```

**Computed by:** New function `_compute_historical_summary()` called during enrichment

### 1.5.7 AI Review Integration

The AI Review Lambda (Phase 2) will use knowledge base + historical data:

```python
def review_with_knowledge(invoice: dict) -> dict:
    """AI review enhanced with AP knowledge and historical patterns."""

    # 1. Load AP knowledge
    vendor_knowledge = get_knowledge(entity_type="vendor", entity_id=invoice['vendor_id'])
    property_knowledge = get_knowledge(entity_type="property", entity_id=invoice['property_id'])
    account_knowledge = get_knowledge(entity_type="account", entity_id=invoice['account_key'])

    # 2. Load historical summary
    history = invoice.get('historical_summary', {})

    # 3. Build review prompt with context
    prompt = f"""
    Review this utility bill using AP knowledge and historical patterns.

    === INVOICE ===
    Vendor: {invoice['vendor_name']}
    Property: {invoice['property_name']}
    Account: {invoice['account_number']}
    Amount: ${invoice['total_amount']:,.2f}
    Lines: {invoice['line_count']}
    Bill Date: {invoice['bill_date']}

    === AP KNOWLEDGE ===
    {format_knowledge_notes(vendor_knowledge + property_knowledge + account_knowledge)}

    === HISTORICAL PATTERNS ===
    Average amount: ${history.get('avg_amount', 'N/A')}
    Typical range: ${history.get('min_amount', 'N/A')} - ${history.get('max_amount', 'N/A')}
    Standard deviation: ${history.get('std_dev', 'N/A')}
    Typical line count: {history.get('typical_line_count', 'N/A')}
    Last 3 bills: {history.get('last_3_bills', [])}
    This bill's anomaly score: {history.get('anomaly_score', 'N/A')}

    === YOUR TASK ===
    1. Compare this invoice against AP knowledge - any violations?
    2. Compare against historical patterns - any anomalies?
    3. Check for common issues mentioned in knowledge base
    4. Determine if this needs human review or can auto-proceed

    Return JSON:
    {{
      "quality_score": 0-100,
      "knowledge_violations": ["Expected range is $80-150, this bill is $250"],
      "historical_anomalies": ["Amount is 2.1 std devs above average"],
      "issues": [...],
      "recommendation": "approve|review|rework",
      "reasoning": "..."
    }}
    """

    return call_gemini(prompt)
```

### 1.5.8 Gamification & Adoption

Encourage APs to document knowledge:

- **Leaderboard:** "Top Knowledge Contributors This Month"
- **Badges:** "Vendor Expert: SCE" (10+ verified notes)
- **Impact Metrics:** "Your notes helped auto-approve 47 bills this week"
- **Verification Rewards:** Points for verifying others' notes

### 1.5.9 Knowledge Import

**Bulk import existing knowledge:**
- Import from Excel/CSV (vendor rules, expected amounts)
- Import from email threads (common Q&A)
- Import from existing documentation

---

## Phase 2: AI Review Shadow Mode ✅ COMPLETE

**Commit:** `pending` (2026-02-12)
**Status:** Implemented, ready for testing

### What Was Implemented

#### Backend (main.py)

1. **Garbage Line Detection**
   - `GARBAGE_LINE_PATTERNS`: 17 regex patterns for balance forward, payments, credits, etc.
   - `_detect_garbage_lines()`: Scans lines and flags potential garbage with confidence scores

2. **Historical Comparison**
   - `_get_account_history()`: Fetches last 10 bills from Stage 7/8 for comparison
   - Compares current bill total vs historical average (flags if > 2 std devs)
   - Compares line counts (flags unusual patterns)

3. **AI Review Endpoints**
   - `POST /api/ai-review/analyze`: Runs deterministic + Gemini analysis
   - `GET /api/ai-review/suggestion/{pdf_id}`: Gets cached suggestion
   - `_gemini_review_bill()`: LLM semantic review for additional insights

4. **Accuracy Tracking**
   - `_track_ai_accuracy()`: Compares AI suggestions to human actions on submit
   - `GET /api/ai-review/stats`: Returns precision, recall, F1 score, auto-pass accuracy

5. **DynamoDB Table**
   - `jrk-bill-ai-suggestions`: Stores suggestions and accuracy records

#### Frontend (review.html)

- AI Review panel with purple styling
- Shows garbage lines detected with "Show Line" action
- Shows historical anomalies
- Shows knowledge base flags
- Displays confidence % and "WOULD AUTO-PASS" / "NEEDS REVIEW" verdict
- "+Gemini" badge when Gemini analysis was used

#### Dashboard (/ai-review-dashboard)

- Overall stats (bills reviewed, auto-pass accuracy, avg confidence)
- Deletion accuracy (TP/FP/FN, precision/recall/F1)
- Recent reviews table
- Rollout criteria info

### Rollout Phases

| Phase | Criteria | Action |
|-------|----------|--------|
| Shadow (current) | Just observe | Show suggestions, track accuracy |
| Assisted | Accuracy > 80% | Pre-select suggested deletions |
| Auto-delete | Accuracy > 95% on deletions | Auto-delete high-confidence garbage |
| Auto-pass | Accuracy > 95% on auto-pass | Skip human review for clean bills |

---

## Phase 2 (Original Plan - For Reference)

### Goal
AI automatically reviews parsed output for quality issues, reducing human review burden.

### 2.1 Create AI Review Lambda

**New File:** `aws_lambdas/us-east-1/jrk-bill-reviewer/code/lambda_bill_reviewer.py`

**Purpose:** After enrichment, AI reviews the parsed data for common issues

**Trigger:** S3 event on `Bill_Parser_4_Enriched_Outputs/`

**Review Checks:**

| Check | Description | Action |
|-------|-------------|--------|
| **Amount Sanity** | Total > $50,000 or negative amounts | Flag for review |
| **Date Sanity** | Bill date in future or > 1 year old | Flag for review |
| **Missing Critical Fields** | No vendor, no account, no amount | Flag for review |
| **Duplicate Detection** | Same account + bill date already exists | Flag as duplicate |
| **Line Count Mismatch** | Expected 15 lines, got 3 | Flag for rework |
| **Consumption Anomaly** | Usage 10x higher than typical | Flag for review |
| **Vendor Confidence** | Enrichment score < 0.8 | Flag for review |

**Implementation:**
```python
def review_parsed_bill(s3_key: str) -> dict:
    """AI-powered review of parsed bill quality."""

    # Load parsed JSONL
    lines = load_jsonl(s3_key)

    issues = []

    # 1. Amount sanity
    total = sum(line.get('Line Item Charge', 0) for line in lines)
    if total > 50000:
        issues.append({
            "type": "HIGH_AMOUNT",
            "severity": "warning",
            "message": f"Total ${total:,.2f} exceeds $50,000 threshold",
            "auto_resolve": False
        })

    # 2. Call Gemini for semantic review
    review_prompt = f"""
    Review this utility bill data for issues:

    Vendor: {lines[0].get('Vendor Name')}
    Account: {lines[0].get('Account Number')}
    Bill Date: {lines[0].get('Bill Date')}
    Total: ${total:,.2f}
    Line Count: {len(lines)}

    Line Items:
    {json.dumps([{
        'description': l.get('Line Item Description'),
        'amount': l.get('Line Item Charge')
    } for l in lines], indent=2)}

    Check for:
    1. Unusual charges (e.g., negative amounts, extremely high fees)
    2. Missing information that should be present
    3. Inconsistencies between lines
    4. Potential parsing errors (truncated descriptions, swapped columns)

    Return JSON:
    {{
      "quality_score": 0-100,
      "issues": [
        {{"type": "...", "severity": "error|warning|info", "message": "...", "line_index": N}}
      ],
      "recommendation": "approve|review|rework"
    }}
    """

    review_result = call_gemini(review_prompt)

    # 3. Take action based on review
    if review_result['recommendation'] == 'approve':
        # Move to next stage automatically
        pass
    elif review_result['recommendation'] == 'review':
        # Flag for human review
        flag_for_review(s3_key, issues + review_result['issues'])
    elif review_result['recommendation'] == 'rework':
        # Send back to parser with feedback
        send_to_rework(s3_key, review_result['issues'])

    return review_result
```

### 2.2 Review Dashboard

**New File:** `templates/ai_review.html`

**Features:**
- List of AI-reviewed bills with quality scores
- Filter by recommendation (approve/review/rework)
- One-click approve for high-confidence bills
- Drill-down to see AI's reasoning
- Override AI decision with feedback (trains future reviews)

**Endpoint:** `/api/ai-review/pending`
```python
@app.get("/api/ai-review/pending")
def get_ai_review_pending():
    """Get bills pending AI review or human override."""
    # Query DynamoDB for review records
    # Return sorted by quality_score ascending (worst first)
```

### 2.3 Feedback Loop

Store human corrections to improve AI review:

**DynamoDB Table:** `jrk-bill-ai-feedback`
```json
{
  "pdf_id": "abc123",
  "ai_recommendation": "approve",
  "human_action": "rework",
  "human_reason": "Missing 3 line items from page 2",
  "timestamp": "2026-02-13T10:00:00Z",
  "user": "analyst@jrk.com"
}
```

**Future:** Use feedback to fine-tune review prompts or train classifier.

---

## Phase 3: AI Billback Assignment (Week 3-4)

### Goal
AI automatically assigns bills to UBI periods with high confidence, humans handle exceptions.

### 3.1 AI Assignment Engine

**New File:** `main.py` - New endpoint `/api/billback/ai-assign`

**Logic:**
```python
def ai_suggest_ubi_assignment(bill: dict) -> dict:
    """AI suggests UBI period assignment based on context."""

    account_key = f"{bill['property_id']}|{bill['vendor_id']}|{bill['account_number']}"

    # 1. Get account history from Stage 8
    history = get_account_ubi_history(account_key)

    # 2. Build context for AI
    context = {
        "account_number": bill['account_number'],
        "service_address": bill['service_address'],
        "bill_period_start": bill['Bill Period Start'],
        "bill_period_end": bill['Bill Period End'],
        "bill_date": bill['Bill Date'],
        "total_amount": bill['total_amount'],
        "utility_type": bill['utility_type'],
        "last_5_assignments": history[-5:],  # Recent UBI assignments
        "typical_bill_frequency": detect_frequency(history),  # monthly, quarterly, etc.
    }

    # 3. AI determines assignment
    prompt = f"""
    Analyze this utility bill and suggest UBI period assignment.

    Bill Details:
    - Account: {context['account_number']}
    - Service Period: {context['bill_period_start']} to {context['bill_period_end']}
    - Bill Date: {context['bill_date']}
    - Amount: ${context['total_amount']:,.2f}
    - Utility: {context['utility_type']}

    Assignment History (last 5):
    {json.dumps(context['last_5_assignments'], indent=2)}

    Typical billing frequency: {context['typical_bill_frequency']}

    Determine:
    1. Which UBI period(s) this bill should be assigned to (MM/YYYY format)
    2. If the bill spans multiple months, how to split the amount
    3. Confidence level (0-100)
    4. Any concerns or flags

    Return JSON:
    {{
      "periods": ["02/2026"],  // or ["01/2026", "02/2026"] for multi-period
      "amounts": [1234.56],    // amount per period
      "split_reason": null,    // or "quarterly bill covering 3 months"
      "confidence": 95,
      "flags": [],             // or ["duplicate_warning", "gap_detected"]
      "reasoning": "Service period 01/15-02/14 maps to February billing cycle"
    }}
    """

    result = call_gemini(prompt)

    return {
        "suggestion": result,
        "auto_assign": result['confidence'] >= 90 and len(result['flags']) == 0,
        "needs_review": result['confidence'] < 90 or len(result['flags']) > 0
    }
```

### 3.2 Auto-Assignment Workflow

```
Bill arrives in Stage 7 (BILLBACK unassigned)
    ↓
AI Assignment Engine analyzes
    ↓
    ├─→ Confidence >= 90%, no flags → AUTO-ASSIGN → Stage 8
    │       (Log decision for audit)
    │
    └─→ Confidence < 90% OR flags → QUEUE FOR REVIEW
            (Show AI suggestion, human confirms/corrects)
```

### 3.3 Configurable Thresholds

**DynamoDB Config:** `jrk-bill-config` with key `AI_ASSIGNMENT_CONFIG`
```json
{
  "auto_assign_threshold": 90,
  "max_auto_assign_amount": 10000,
  "require_human_review_flags": ["duplicate_warning", "gap_detected", "new_account"],
  "enabled_properties": ["*"],  // or specific property IDs
  "disabled_vendors": [],
  "audit_sample_rate": 0.1  // 10% random audit
}
```

### 3.4 Assignment Dashboard Enhancement

**File:** `templates/billback.html`

**New Features:**
- "AI Suggested" badge on bills with high-confidence suggestions
- One-click accept for AI suggestions
- "Auto-Assigned" section showing what AI did automatically
- Audit trail: "Assigned by AI on 2026-02-13 with 95% confidence"

### 3.5 Multi-Period Intelligence

**Handle complex scenarios:**

| Scenario | AI Behavior |
|----------|-------------|
| Quarterly bill | Split into 3 periods with 1/3 each |
| Bill spans month boundary | Assign to service end month |
| Catch-up bill (missed month) | Detect gap, suggest backfill |
| Duplicate service period | Flag as potential duplicate |
| New account (no history) | Lower confidence, require review |

---

## Phase 4: AI Q&A Assistant (Week 5-6)

### Goal
Users can ask questions about invoices and the system, AI answers using context.

### 4.1 Chat Interface

**New File:** `templates/ai_chat.html`

**Features:**
- Floating chat widget on all pages
- Context-aware: knows which invoice you're viewing
- Can answer questions like:
  - "Why was this bill flagged?"
  - "What's the typical amount for this account?"
  - "When was the last bill for this service address?"
  - "Why is this amount different from last month?"

### 4.2 Chat API Endpoint

**File:** `main.py` - New endpoint `/api/ai/chat`

```python
@app.post("/api/ai/chat")
async def ai_chat(
    message: str = Form(...),
    context_type: str = Form(None),  # "invoice", "billback", "general"
    context_id: str = Form(None),    # pdf_id, account_key, etc.
    user: str = Depends(require_user)
):
    """AI-powered chat assistant for bill review questions."""

    # 1. Build context based on what user is viewing
    context = {}
    if context_type == "invoice" and context_id:
        context = load_invoice_context(context_id)
    elif context_type == "billback" and context_id:
        context = load_billback_context(context_id)

    # 2. Add system knowledge
    system_prompt = """
    You are an AI assistant for JRK's Bill Review platform.

    You help users with:
    - Understanding parsed invoice data
    - Explaining why bills were flagged or need review
    - Answering questions about billing history
    - Guiding users through the billback assignment process
    - Troubleshooting issues with parsing or enrichment

    You have access to:
    - The current invoice/bill being viewed
    - Historical data for the same account
    - System configuration and rules

    Be concise and helpful. If you don't know something, say so.
    """

    # 3. Call Gemini with context
    response = call_gemini(
        system_prompt + f"\n\nContext:\n{json.dumps(context)}\n\nUser: {message}"
    )

    # 4. Log conversation for improvement
    log_chat(user, message, response, context_type, context_id)

    return {"response": response}
```

### 4.3 Context Types

| Context | Data Loaded | Example Questions |
|---------|-------------|-------------------|
| **Invoice** | All line items, enrichment, history | "Why is vendor confidence low?" |
| **Billback** | UBI history, assignments, gaps | "What period should this go to?" |
| **Account** | All bills for account, patterns | "Show me billing trend for this account" |
| **Property** | All accounts/vendors for property | "Which vendors have pending bills?" |
| **General** | System help, how-to guides | "How do I send a bill to rework?" |

### 4.4 Proactive Suggestions

AI can proactively offer help:
- "I notice this bill is 40% higher than usual. Would you like me to investigate?"
- "This account has a gap in assignments. Want me to find the missing bill?"
- "3 similar bills are waiting - want me to suggest assignments for all of them?"

### 4.5 Chat History & Learning

**DynamoDB Table:** `jrk-bill-chat-history`
```json
{
  "session_id": "uuid",
  "user": "analyst@jrk.com",
  "messages": [
    {"role": "user", "content": "Why was this flagged?", "timestamp": "..."},
    {"role": "assistant", "content": "This bill was flagged because...", "timestamp": "..."}
  ],
  "context_type": "invoice",
  "context_id": "abc123",
  "helpful_rating": 5  // User feedback
}
```

---

## Phase 5: Continuous Improvement (Ongoing)

### 5.1 Metrics Dashboard

Track AI performance:
- Parser accuracy (lines correct vs total)
- Review accuracy (AI recommendation vs human action)
- Assignment accuracy (AI assignment vs human correction)
- Chat helpfulness (user ratings)

### 5.2 Feedback Loops

```
Human corrects AI → Feedback stored → Prompts improved → AI gets better
```

### 5.3 Model Upgrades

As newer Gemini models release:
- Test on sample set
- Compare accuracy metrics
- Roll out gradually with A/B testing

---

## Technical Requirements

### Infrastructure

| Component | Current | Needed |
|-----------|---------|--------|
| **Gemini API Keys** | 3 keys for parsing | Add keys for review + chat |
| **Lambda Functions** | 6 bill-related | +1 reviewer Lambda |
| **DynamoDB Tables** | 5 tables | +2 (ai-feedback, chat-history) |
| **API Endpoints** | ~50 endpoints | +5 AI endpoints |

### Cost Estimates

| Feature | Gemini Calls/Day | Est. Cost/Month |
|---------|------------------|-----------------|
| Parsing (current) | ~500 | ~$50 |
| AI Review | ~500 | ~$25 (Flash) |
| AI Assignment | ~300 | ~$15 (Flash) |
| AI Chat | ~200 | ~$10 (Flash) |
| **Total** | ~1500 | **~$100/month** |

### Security Considerations

- Chat logs may contain sensitive data → encrypt at rest
- AI decisions need audit trail → log all auto-actions
- Rate limit chat API → prevent abuse
- Don't expose raw Gemini responses → sanitize outputs

---

## Implementation Timeline

| Week | Phase | Deliverables |
|------|-------|--------------|
| 1 | Chunk Mapping | Page metadata in JSONL, UI badges, jump-to-page |
| 2 | AI Review | Review Lambda, quality scoring, review dashboard |
| 3 | AI Assignment (Part 1) | Suggestion engine, confidence scoring |
| 4 | AI Assignment (Part 2) | Auto-assignment, thresholds, audit trail |
| 5 | AI Chat (Part 1) | Chat API, basic Q&A, invoice context |
| 6 | AI Chat (Part 2) | Proactive suggestions, chat history, learning |
| 7+ | Polish & Iterate | Metrics, feedback loops, model tuning |

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Human review time per bill | ~2 min | ~30 sec (90% auto) |
| Bills auto-assigned | 0% | 70%+ |
| Parser rework rate | ~5% | ~2% (AI catches issues) |
| User questions to support | ~20/day | ~5/day (AI answers) |
| Time to billback completion | ~3 days | ~1 day |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| AI makes wrong assignments | Confidence thresholds, audit sampling, easy undo |
| Users don't trust AI | Show reasoning, gradual rollout, prove accuracy |
| Gemini API costs spike | Rate limiting, caching, use Flash for simple tasks |
| Gemini API downtime | Graceful degradation to manual workflow |
| Data privacy concerns | Don't send PII to chat, anonymize where possible |

---

## Next Steps

1. **Approve plan scope** - Which phases to prioritize?
2. **Set up test environment** - Deploy Lambdas to staging
3. **Create sample dataset** - Multi-page bills for testing chunk mapping
4. **Define success criteria** - What accuracy % is acceptable for auto-assign?
5. **Start Phase 1** - Chunk-to-line mapping implementation
