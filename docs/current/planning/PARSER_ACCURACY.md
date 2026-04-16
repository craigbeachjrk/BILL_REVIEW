# Bill Parser Accuracy Improvement Plan

**Date:** March 19, 2026
**Status:** Proposed
**Author:** Craig Beach / Claude Code

---

## Executive Summary

The bill parsing pipeline processes ~15,000 utility bill PDFs through a multi-stage Lambda pipeline: Route → Parse → Enrich → Review. Analysis of the actual Lambda code reveals 8 fundamental weaknesses causing inaccurate vendor matching, property identification, GL assignment, and line item extraction. This plan proposes 10 ordered improvements that address each weakness, starting with the highest-impact changes.

---

## Table of Contents

1. [Current Architecture](#1-current-architecture)
2. [Root Causes of Inaccuracy](#2-root-causes-of-inaccuracy)
3. [Step 1: Migrate Standard Parser to JSON Mode](#step-1)
4. [Step 2: Build Property Reference Directory](#step-2)
5. [Step 3: Inject Reference Data into Parser Prompt](#step-3)
6. [Step 4: Build Vendor Alternate Names Directory](#step-4)
7. [Step 5: Header Normalization for Standard Parser](#step-5)
8. [Step 6: Per-Field Confidence Scoring](#step-6)
9. [Step 7: Automatic Post-Enrichment Validation](#step-7)
10. [Step 8: OCR Fallback for Scanned PDFs](#step-8)
11. [Step 9: Vendor-Specific Parsing Templates](#step-9)
12. [Step 10: Enrichment Cache Unification](#step-10)

---

## 1. Current Architecture

### Pipeline Flow

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   ROUTER     │    │   PARSER     │    │  ENRICHER    │    │   REVIEW     │
│              │    │              │    │              │    │   (main.py)  │
│ S1 → S1_Std  │───▶│ S1 → S3     │───▶│ S3 → S4     │───▶│ S4 → S6/S7  │
│   or S1_Lg   │    │              │    │              │    │              │
│              │    │ Gemini 2.5   │    │ Vendor match │    │ Human QC     │
│ Count pages  │    │ Pro (text)   │    │ Property     │    │ GL overrides │
│ Check size   │    │              │    │ GL assign    │    │ Submit/Post  │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
       │                                       │
       ▼                                       ▼
┌──────────────┐                      ┌──────────────┐
│ CHUNK PROC   │                      │ Reference    │
│              │                      │ Data (S3)    │
│ Large files  │                      │              │
│ 2-page split │                      │ dim_vendor   │
│ JSON mode ✓  │                      │ dim_property │
│ Aggregator   │                      │ dim_gl_acct  │
└──────────────┘                      └──────────────┘
```

### Key Lambda Files

| Lambda | File | Purpose |
|--------|------|---------|
| `jrk-bill-router` | `lambda_bill_router.py` | Routes PDFs by page count/size |
| `jrk-bill-parser` | `lambda_bill_parser.py` | Standard parser (≤10 pages) — Gemini 2.5 Pro, **pipe-delimited output** |
| `jrk-bill-chunk-processor` | `lambda_chunk_processor.py` | Large file parser — Gemini, **JSON mode output** |
| `jrk-bill-aggregator` | `lambda_bill_aggregator.py` | Combines chunk results, normalizes headers |
| `jrk-bill-enricher` | `lambda_bill_enricher.py` | Vendor/property matching, GL assignment |
| `vendor-cache-builder` | `build_vendor_cache.py` | Refreshes vendor/property/GL caches from Entrata |

### Current Output Schema (30 columns)

```
Vendor Name | Bill To Name First Line | Bill To Name Second Line | Bill To Address |
Bill To City State Zip | Account Number | Meter Number | Bill Date | Due Date |
Service Start Date | Service End Date | Service Address | Service City State Zip |
Days of Service | Meter Read Current | Meter Read Previous | Usage Quantity |
Usage UOM | Rate | Tax Amount | Charge Amount | Charge Description |
Charge Code | Charge Category | Description | GL Account Name |
GL Account Number | House or Vacant | Inferred Fields | Notes
```

---

## 2. Root Causes of Inaccuracy

### Problem 1: Pipe-Delimited Output Format (Standard Parser)

**Where:** `lambda_bill_parser.py` lines 243-258 (PROMPT), lines 300-340 (`normalize_row()`)

**What happens:** The standard parser asks Gemini to return data as pipe-delimited text:
```
Southern California Edison|Sunset Ridge Apartments|c/o JRK Residential|...
```

If ANY field contains a pipe character (e.g., "Water | Sewer Service"), the entire row shifts. The `normalize_row()` function tries to repair this by joining "extra" columns into the description field, but this heuristic is unreliable and silently corrupts data.

**Evidence:** The chunk processor (`lambda_chunk_processor.py` line 394) already uses JSON mode (`responseMimeType: "application/json"`) and does NOT have this problem. This proves JSON mode works with the same Gemini API.

**Impact:** Affects every bill where any field contains a pipe, comma-pipe sequence, or the model decides to add extra delimiters. Estimated 5-15% of bills.

---

### Problem 2: No Reference Data at Parse Time

**Where:** `lambda_bill_parser.py` PROMPT (line 243) — contains zero context about known vendors, properties, or accounts

**What happens:** The parser prompt says "Extract these 30 fields from this PDF" with no context about what values are expected. Gemini guesses:
- **Vendor Name** → might return "SoCalEdison" or "So. Cal. Edison" or "SCE"
- **Bill To Name** → might return the management company instead of the property
- **Account Number** → might miss leading zeros or include dashes inconsistently

Then the enricher must fuzzy-match these guesses to the actual reference data. This is a lossy two-step process.

**Impact:** This is the #1 cause of vendor and property mismatches. Every bill goes through this guess-then-match pipeline.

---

### Problem 3: Weak Vendor Matching

**Where:** `lambda_bill_enricher.py` lines 750-828

**What happens:** Three-tier matching:
1. Exact normalized match (works for "Southern California Edison" = "SOUTHERN CALIFORNIA EDISON")
2. Case-insensitive match (minimal improvement)
3. Gemini 1.5 Flash with up to 500 candidates in a single prompt

Step 3 is expensive and unreliable — the prompt is generic ("Compare target to candidates by semantics") with no geographic or utility-type context.

**Impact:** Abbreviations ("SCE" vs "Southern California Edison"), regional name variants ("PG&E" vs "Pacific Gas and Electric"), and DBA names regularly fail.

---

### Problem 4: Property Matching Relies on Bill-To Name

**Where:** `lambda_bill_enricher.py` lines 787-824

**What happens:** The enricher matches `Bill To Name First Line` to property names. But utility bills frequently list the management company ("JRK Residential") in the Bill-To field, not the property name. The `Service Address` is used for secondary matching, but only when it contains a recognizable street number.

**Impact:** Properties with management company in Bill-To and P.O. Box service addresses regularly mismatch.

---

### Problem 5: No Header Normalization for Standard Parser

**Where:** `lambda_bill_parser.py` `write_ndjson()` function — writes rows directly without cross-line normalization

**What happens:** For a multi-line-item bill, each row may have slightly different values for header fields:
- Row 1: Vendor Name = "Southern California Edison"
- Row 2: Vendor Name = "So Cal Edison"
- Row 3: Vendor Name = "" (blank, Gemini skipped it)

The aggregator Lambda (`lambda_bill_aggregator.py`) uses `Counter.most_common()` to normalize these across chunks. But the standard parser writes rows directly without this normalization.

**Impact:** Inconsistent header fields on standard-path bills (the majority of bills).

---

### Problem 6: No Confidence Scoring

**Where:** PROMPT column 29 "Inferred Fields" — model self-reports which fields it guessed

**What happens:** The only signal for extraction confidence is the `Inferred Fields` column, which is a hyphen-separated list of field names the model claims it inferred. This is unreliable because:
- The model doesn't always accurately report inferences
- Binary (inferred vs. not) doesn't capture degrees of confidence
- No per-field granularity in the output

**Impact:** Low-confidence extractions pass through to review without flagging. Reviewers must manually inspect every field.

---

### Problem 7: No Post-Enrichment Validation

**Where:** `lambda_bill_enricher.py` `_enrich_lines()` — writes output immediately after enrichment

**What happens:** After the enricher assigns vendor, property, and GL codes, the result is written directly to Stage 4 with no validation. Bad matches pass through silently:
- Vendor in California matched to a property in Texas
- Account number doesn't match the vendor's known pattern
- Bill amount is 10x the historical average

**Impact:** Bad enrichment reaches the review stage, requiring human detection.

---

### Problem 8: No OCR Fallback

**Where:** `lambda_bill_parser.py` `call_gemini_rest()` — sends raw PDF bytes via `inline_data`

**What happens:** Raw PDF bytes are base64-encoded and sent to Gemini. For born-digital PDFs with embedded text, this works well. For scanned/image-only PDFs, Gemini's internal vision model must do OCR, which is less reliable than dedicated OCR tools.

**Impact:** Affects scanned PDFs (minority of volume, but failures are total — zero data extracted).

---

## Step 1: Migrate Standard Parser to JSON Mode {#step-1}

**Impact:** HIGH — eliminates column-shift errors
**Effort:** 2-3 days
**File:** `aws_lambdas/us-east-1/jrk-bill-parser/code/lambda_bill_parser.py`

### What to Change

The chunk processor already proves JSON mode works. Port its pattern to the standard parser.

### Current State (Standard Parser — Pipe-Delimited)

```python
# lambda_bill_parser.py line 243
PROMPT = """You are a utility-bill data-extraction engine...
Return one line per charge/line-item found on every page,
using PIPE (|) as delimiter with exactly 30 columns..."""

# line 347 — Gemini call
payload = {
    "contents": [{"parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "application/pdf", "data": b64_pdf}}
    ]}]
}
```

### Target State (JSON Mode)

```python
# Updated PROMPT — request JSON array
PROMPT = """You are a utility-bill data-extraction engine.
Analyze the attached PDF and extract every charge/line-item.

Return a JSON array where each element is an object with these exact keys:

{
  "vendor_name": "string — the utility company name",
  "bill_to_name_line1": "string — first line of the bill-to / customer name",
  "bill_to_name_line2": "string — second line (if any)",
  "bill_to_address": "string — mailing address",
  "bill_to_city_state_zip": "string — city, state, zip",
  "account_number": "string — customer account number",
  "meter_number": "string — meter ID (if shown)",
  "bill_date": "string — MM/DD/YYYY format",
  "due_date": "string — MM/DD/YYYY format",
  "service_start_date": "string — MM/DD/YYYY",
  "service_end_date": "string — MM/DD/YYYY",
  "service_address": "string — physical service location",
  "service_city_state_zip": "string — city, state, zip",
  "days_of_service": "integer or empty string",
  "meter_read_current": "string",
  "meter_read_previous": "string",
  "usage_quantity": "string — numeric usage value",
  "usage_uom": "string — kWh, therms, gallons, CCF, etc.",
  "rate": "string — per-unit rate",
  "tax_amount": "string — tax for this line",
  "charge_amount": "string — charge for this line (REQUIRED, never empty)",
  "charge_description": "string — line item description from the bill",
  "charge_code": "string — utility company's charge code if shown",
  "charge_category": "string — Delivery, Supply, Tax, Fee, Credit, etc.",
  "description": "string — additional details",
  "gl_account_name": "string — leave empty",
  "gl_account_number": "string — leave empty",
  "house_or_vacant": "string — HOUSE or VACANT based on bill-to name",
  "inferred_fields": "string — comma-separated list of fields you inferred",
  "notes": "string — any extraction notes"
}

Rules:
- Return ONLY the JSON array. No markdown, no explanation.
- One object per charge/line-item.
- charge_amount MUST be a valid number (no dollar signs, commas OK).
- Dates MUST be MM/DD/YYYY format.
- If a field is not found on the bill, use an empty string "".
- For multi-page bills, extract ALL line items from ALL pages.
- Header fields (vendor, bill_to, account, dates) repeat on every line item.
"""

# Updated Gemini call — add JSON mode
payload = {
    "contents": [{"parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "application/pdf", "data": b64_pdf}}
    ]}],
    "generationConfig": {
        "responseMimeType": "application/json",
        "temperature": 0.1
    }
}
```

### Response Parsing Changes

```python
# BEFORE (pipe-delimited):
def parse_response(text):
    rows = []
    for line in text.strip().split('\n'):
        if '|' not in line:
            continue
        parts = line.split('|')
        row = normalize_row(parts)  # fragile repair logic
        if validate_row_content(row):
            rows.append(row)
    return rows

# AFTER (JSON mode):
JSON_TO_COLUMN = {
    "vendor_name": "Vendor Name",
    "bill_to_name_line1": "Bill To Name First Line",
    "bill_to_name_line2": "Bill To Name Second Line",
    "bill_to_address": "Bill To Address",
    "bill_to_city_state_zip": "Bill To City State Zip",
    "account_number": "Account Number",
    "meter_number": "Meter Number",
    "bill_date": "Bill Date",
    "due_date": "Due Date",
    "service_start_date": "Service Start Date",
    "service_end_date": "Service End Date",
    "service_address": "Service Address",
    "service_city_state_zip": "Service City State Zip",
    "days_of_service": "Days of Service",
    "meter_read_current": "Meter Read Current",
    "meter_read_previous": "Meter Read Previous",
    "usage_quantity": "Usage Quantity",
    "usage_uom": "Usage UOM",
    "rate": "Rate",
    "tax_amount": "Tax Amount",
    "charge_amount": "Charge Amount",
    "charge_description": "Charge Description",
    "charge_code": "Charge Code",
    "charge_category": "Charge Category",
    "description": "Description",
    "gl_account_name": "GL Account Name",
    "gl_account_number": "GL Account Number",
    "house_or_vacant": "House or Vacant",
    "inferred_fields": "Inferred Fields",
    "notes": "Notes",
}

def parse_json_response(text):
    """Parse JSON array response from Gemini."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
        text = text.strip()

    items = json.loads(text)
    if not isinstance(items, list):
        items = [items]

    rows = []
    for item in items:
        row = {}
        for json_key, col_name in JSON_TO_COLUMN.items():
            row[col_name] = str(item.get(json_key, "")).strip()
        # Validate charge_amount is present and numeric
        amt = row.get("Charge Amount", "").replace(",", "").replace("$", "")
        if amt and amt.replace(".", "").replace("-", "").isdigit():
            rows.append(row)
    return rows
```

### What to Remove

- `normalize_row()` function — no longer needed
- `_normalize_reply()` function — no longer needed
- Pipe-splitting logic in `call_gemini_with_retry_rest()`
- Column count validation (JSON handles this inherently)

### Testing

1. Process 10 known-good bills through both old and new parsers
2. Compare field-by-field accuracy
3. Specifically test bills that previously had column-shift errors
4. Test with bills that have pipe characters in descriptions/addresses

---

## Step 2: Build Property Reference Directory {#step-2}

**Impact:** HIGH — dramatically improves property matching
**Effort:** 3-4 days
**Files:** New Lambda + changes to `lambda_bill_enricher.py`

### What to Build

A comprehensive property directory stored at `s3://jrk-analytics-billing/Bill_Parser_Enrichment/reference/property_directory.json`:

```json
{
  "built_at": "2026-03-19T12:00:00Z",
  "properties": [
    {
      "property_id": "12345",
      "property_name": "Sunset Ridge Apartments",
      "property_code": "SUNR",
      "state": "CA",
      "addresses": [
        "1234 Main St, San Diego, CA 92101",
        "1236 Main Street, San Diego, CA 92101"
      ],
      "alternate_names": [
        "Sunset Ridge",
        "Sunset Ridge Apts",
        "SUNSET RIDGE APARTMENTS"
      ],
      "known_accounts": {
        "Southern California Edison": ["3-123-456-7890", "3-123-456-7891"],
        "San Diego Gas & Electric": ["0987654321"],
        "City of San Diego": ["W-9999-001"]
      }
    }
  ]
}
```

### How to Build It

**Source 1: `dim_property` (existing)**
- Property name, ID, state, code
- Already loaded by the enricher

**Source 2: Historical JSONL from S7/S8/S9 stages**
- These are enriched bills that have been verified by humans
- Parse filenames for Property-Vendor-Account mapping (already done by `_load_or_build_bill_index()`)
- Extract `Service Address` and `Bill To Name` from the JSONL content for top matches
- This is a one-time bulk job, then incremental updates

**Source 3: `accounts_to_track` config**
- Contains property→vendor→account mappings that users have manually configured
- Already stored in `jrk-bill-config` DynamoDB table

### Build Process

Create a new Lambda `jrk-property-directory-builder`:

```python
def lambda_handler(event, context):
    """Build property reference directory from multiple sources."""

    # 1. Load dim_property (name, ID, state, code)
    properties = load_dim_property()

    # 2. Load accounts_to_track (property→vendor→account mappings)
    accounts_track = load_accounts_to_track()
    for account in accounts_track:
        pid = account["propertyId"]
        vendor = account["vendorName"]
        acct_num = account["accountNumber"]
        if pid in properties:
            properties[pid]["known_accounts"].setdefault(vendor, []).append(acct_num)

    # 3. Mine historical JSONL filenames for address data
    # Filenames: Property-Vendor-Account-StartDate-EndDate-BillDate_timestamp.jsonl
    for stage in ["Bill_Parser_7_PostEntrata_Submission/",
                   "Bill_Parser_8_UBI_Assigned/"]:
        for key in list_s3_keys(stage):
            parts = parse_filename(key)
            if parts and parts["property_id"] in properties:
                # Read first line of JSONL for address data (sample only)
                first_line = read_first_jsonl_line(key)
                if first_line:
                    addr = first_line.get("Service Address", "")
                    city_st_zip = first_line.get("Service City State Zip", "")
                    if addr:
                        full_addr = f"{addr}, {city_st_zip}".strip(", ")
                        properties[parts["property_id"]]["addresses"].add(full_addr)

    # 4. Build alternate names from historical Bill To Name variations
    # Group by property ID, collect all Bill To Name First Line values
    for pid, prop in properties.items():
        names = set()
        names.add(prop["property_name"])
        names.add(prop["property_name"].upper())
        # Add abbreviated versions
        for word in ["Apartments", "Apartment", "Apts", "Apt"]:
            if word in prop["property_name"]:
                names.add(prop["property_name"].replace(word, "").strip())
        prop["alternate_names"] = list(names)

    # 5. Write to S3
    output = {"built_at": now_iso(), "properties": list(properties.values())}
    s3.put_object(
        Bucket=BUCKET,
        Key="Bill_Parser_Enrichment/reference/property_directory.json",
        Body=json.dumps(output),
        ContentType="application/json"
    )
```

### How the Enricher Uses It

Update `lambda_bill_enricher.py` to load the property directory and use it for matching:

```python
def _match_property(bill_to_name, service_address, service_state, prop_directory):
    """Match a bill to a property using the reference directory."""

    candidates = []

    for prop in prop_directory:
        score = 0

        # 1. Check if service address matches any known address
        if service_address:
            for known_addr in prop["addresses"]:
                if _addr_similarity(service_address, known_addr) > 0.8:
                    score += 50  # Strong signal

        # 2. Check if bill-to name matches property name or alternates
        if bill_to_name:
            for name in prop["alternate_names"]:
                if _name_similarity(bill_to_name, name) > 0.85:
                    score += 30

        # 3. Check state match
        if service_state and prop["state"] == service_state:
            score += 10

        # 4. Check if account number matches any known account
        # (account_number from the parsed bill vs known_accounts)
        # This is the strongest signal when available

        if score > 0:
            candidates.append((score, prop))

    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    if candidates and candidates[0][0] >= 40:
        return candidates[0][1]  # High-confidence match

    return None  # Fall back to existing Gemini-based matching
```

### Schedule

Run the builder Lambda:
- Weekly via EventBridge scheduled rule
- On-demand via API endpoint in main.py

---

## Step 3: Inject Reference Data into Parser Prompt {#step-3}

**Impact:** HIGH — gives the model context for accurate extraction
**Effort:** 2-3 days
**Files:** `lambda_bill_parser.py`, `lambda_chunk_processor.py`

### The Core Idea

Instead of asking Gemini to guess vendor/property/account from scratch, tell it what to expect:

```
REFERENCE DATA — Use this to improve extraction accuracy:

Known Vendor: Southern California Edison (SCE)
Account Number Pattern: D-DDD-DDD-DDDD (where D = digit)
Known Properties served by this vendor:
  - Sunset Ridge Apartments (1234 Main St, San Diego, CA 92101) — Acct 3-123-456-7890
  - Pacific Gardens (5678 Ocean Blvd, Los Angeles, CA 90015) — Acct 3-234-567-8901
  - Harbor View (910 Bay Dr, Long Beach, CA 90802) — Acct 3-345-678-9012

Common charge types for SCE:
  Delivery Service, Generation, Public Purpose Programs, Franchise Fee,
  Nuclear Decommissioning, Competition Transition Charge, DWR Bond Charge

IMPORTANT: Match the bill's service address and account number to the correct
property from the list above. Format the account number using the pattern shown.
```

### How to Get the Vendor Hint

The vendor hint comes from several sources (in priority order):

1. **Sidecar metadata** — When bills are imported from the scraper, the provider is known. Store it as `notes.json` alongside the PDF in S1.
2. **Email subject/sender** — When bills arrive via email ingest, the sender domain often identifies the vendor.
3. **Knowledge base** — The `fetch_knowledge_notes()` function already pulls vendor-specific instructions from DynamoDB.
4. **Previous parse** — On rework, the original vendor is known from the S4 JSONL.
5. **Filename pattern** — Some filenames contain vendor or account info (e.g., scraper files: `0032400394_20240304.pdf`).

### Implementation

```python
def build_reference_context(vendor_hint=None, account_hint=None):
    """Build reference data block for the parser prompt."""
    if not vendor_hint:
        return ""

    # Load reference directories (cached in Lambda memory)
    vendor_dir = load_vendor_directory()
    prop_dir = load_property_directory()

    # Find matching vendor
    vendor = None
    for v in vendor_dir:
        if vendor_hint.lower() in [v["vendor_name"].lower()] + [n.lower() for n in v.get("alternate_names", [])]:
            vendor = v
            break

    if not vendor:
        return ""

    lines = ["", "REFERENCE DATA — Use this to improve extraction accuracy:", ""]
    lines.append(f"Known Vendor: {vendor['vendor_name']}")

    if vendor.get("account_number_pattern"):
        lines.append(f"Account Number Pattern: {vendor['account_number_pattern']}")

    # Find properties that have this vendor in their known_accounts
    matched_props = []
    for prop in prop_dir:
        if vendor["vendor_name"] in prop.get("known_accounts", {}):
            accounts = prop["known_accounts"][vendor["vendor_name"]]
            addr = prop["addresses"][0] if prop.get("addresses") else ""
            matched_props.append({
                "name": prop["property_name"],
                "address": addr,
                "accounts": accounts,
            })

    if matched_props:
        lines.append(f"Known Properties served by {vendor['vendor_name']}:")
        for p in matched_props[:30]:  # Limit to 30 to stay within prompt size
            accts = ", ".join(p["accounts"][:3])
            lines.append(f"  - {p['name']} ({p['address']}) — Acct {accts}")

    if vendor.get("common_line_items"):
        lines.append(f"\nCommon charge types: {', '.join(vendor['common_line_items'])}")

    lines.append("")
    lines.append("IMPORTANT: Match the bill's service address and account number to the")
    lines.append("correct property from the list above. Format the account number using the pattern shown.")
    lines.append("")

    return "\n".join(lines)
```

Then inject into the prompt:

```python
reference_context = build_reference_context(vendor_hint, account_hint)
full_prompt = PROMPT + reference_context
```

### Where Vendor Hints Come From at Import Time

Update the **scraper import** to attach vendor metadata:

```python
# In api_scraper_import (main.py)
dest_key = f"{INPUT_PREFIX}{ts}_scraper_{orig_filename}"

# Write sidecar metadata alongside the PDF
sidecar = {
    "source": "scraper",
    "provider": provider_name,      # from the scraper API
    "account_number": account_id,   # from the S3 folder name
    "imported_at": ts,
    "imported_by": user,
}
sidecar_key = dest_key.replace(".pdf", "_notes.json").replace(".PDF", "_notes.json")
s3.put_object(Bucket=BUCKET, Key=sidecar_key, Body=json.dumps(sidecar))
```

---

## Step 4: Build Vendor Alternate Names Directory {#step-4}

**Impact:** MEDIUM-HIGH — catches abbreviation mismatches without AI calls
**Effort:** 2 days
**Files:** `vendor-cache-builder`, `lambda_bill_enricher.py`

### What to Build

Extend the vendor cache to include alternate names:

```json
{
  "vendor_id": "V-001",
  "vendor_name": "Southern California Edison",
  "alternate_names": [
    "SCE",
    "SoCal Edison",
    "So Cal Edison",
    "SOUTHERN CALIFORNIA EDISON",
    "S. California Edison",
    "So. Cal. Edison"
  ],
  "utility_types": ["electric"],
  "account_number_pattern": "^\\d-\\d{3}-\\d{3}-\\d{4}$",
  "geographic_coverage": ["CA"],
  "common_line_items": [
    "Delivery Service", "Generation", "Public Purpose Programs",
    "Franchise Fee", "Nuclear Decommissioning"
  ]
}
```

### How to Build Alternate Names

**Source 1: Historical parsed data (automated)**
Mine Stage 3 (pre-enrichment) JSONL files for the raw `Vendor Name` field. Group by the enriched vendor ID from Stage 4. Each unique raw name becomes an alternate name:

```python
# Pseudocode for mining alternates
alternates = defaultdict(set)  # enriched_vendor_id -> set of raw names

for s4_key in list_stage4_keys():
    rows = read_jsonl(s4_key)
    for row in rows:
        enriched_id = row.get("EnrichedVendorID")
        raw_name = row.get("Vendor Name", "").strip()
        if enriched_id and raw_name:
            alternates[enriched_id].add(raw_name)
```

**Source 2: Manual curation (top 20 vendors)**
For the 20 highest-volume vendors, manually add common abbreviations, DBAs, and regional variants.

**Source 3: Knowledge base notes**
The knowledge base already allows vendor-specific instructions. Extend to include alternate names.

### How the Enricher Uses Alternate Names

Before falling through to the expensive Gemini fuzzy-match, check alternate names:

```python
def _match_vendor_with_alternates(target_name, vendor_directory):
    """Try alternate name matching before Gemini."""
    target_norm = target_name.strip().upper()

    for vendor in vendor_directory:
        # Check primary name
        if target_norm == vendor["vendor_name"].upper():
            return vendor

        # Check alternate names
        for alt in vendor.get("alternate_names", []):
            if target_norm == alt.upper():
                return vendor

            # Also check if target contains the alternate (or vice versa)
            if len(alt) >= 3 and (alt.upper() in target_norm or target_norm in alt.upper()):
                return vendor  # substring match

    return None  # Fall through to Gemini
```

---

## Step 5: Header Normalization for Standard Parser {#step-5}

**Impact:** MEDIUM — prevents inconsistent header fields
**Effort:** 1 day
**File:** `lambda_bill_parser.py`

### What to Change

After the standard parser extracts all rows, apply cross-row normalization for header fields. The aggregator already does this for chunked bills — port that logic:

```python
HEADER_FIELDS = [
    "Vendor Name", "Bill To Name First Line", "Bill To Name Second Line",
    "Bill To Address", "Bill To City State Zip", "Account Number",
    "Bill Date", "Due Date", "Service Address", "Service City State Zip",
    "Service Start Date", "Service End Date"
]

def normalize_headers(rows):
    """Use most-common value for header fields across all rows."""
    if len(rows) <= 1:
        return rows

    # Find most common value for each header field
    best = {}
    for field in HEADER_FIELDS:
        values = [r.get(field, "").strip() for r in rows if r.get(field, "").strip()]
        if values:
            counter = Counter(values)
            best[field] = counter.most_common(1)[0][0]

    # Apply to all rows
    for row in rows:
        for field, value in best.items():
            if not row.get(field, "").strip():
                row[field] = value  # Fill blanks with most common
            elif field in ("Vendor Name", "Account Number", "Bill Date", "Due Date"):
                row[field] = value  # Force consistency for critical fields

    return rows
```

Add this call in `write_ndjson()` before writing output:

```python
def write_ndjson(rows, dest_key):
    rows = normalize_headers(rows)  # ADD THIS LINE
    # ... existing write logic ...
```

---

## Step 6: Per-Field Confidence Scoring {#step-6}

**Impact:** MEDIUM — enables smart routing of low-confidence bills
**Effort:** 2 days
**Files:** `lambda_bill_parser.py`, `lambda_chunk_processor.py`

### What to Change

Replace the binary `Inferred Fields` column with per-field confidence:

```json
{
  "vendor_name": "Southern California Edison",
  "vendor_name_confidence": "high",
  "account_number": "3123456789",
  "account_number_confidence": "high",
  "bill_to_name_line1": "JRK Residential",
  "bill_to_name_line1_confidence": "medium",
  "service_address": "1234 Main St",
  "service_address_confidence": "high",
  "charge_amount": "142.56",
  "charge_amount_confidence": "high",
  "charge_description": "Delivery Service",
  "charge_description_confidence": "high",
  ...
}
```

Add to the prompt:
```
For each field, also provide a confidence level as a separate key with suffix "_confidence":
- "high" = clearly visible text on the bill
- "medium" = inferred from context or partially visible
- "low" = guessed or uncertain

Example: "account_number": "12345", "account_number_confidence": "high"
```

### How the Enricher Uses Confidence

```python
# In the enricher, aggregate confidence across all fields
def compute_bill_confidence(rows):
    """Compute overall confidence score for a bill."""
    critical_fields = ["vendor_name", "account_number", "charge_amount", "bill_date"]
    low_count = 0
    for row in rows:
        for field in critical_fields:
            if row.get(f"{field}_confidence") == "low":
                low_count += 1

    if low_count == 0:
        return "high"
    elif low_count <= 2:
        return "medium"
    else:
        return "low"
```

Bills with `"low"` overall confidence get routed to `Bill_Parser_9_Flagged_Review/` instead of Stage 4.

---

## Step 7: Automatic Post-Enrichment Validation {#step-7}

**Impact:** MEDIUM — catches enrichment errors before review
**Effort:** 3-4 days
**File:** `lambda_bill_enricher.py`

### Validation Rules

Add a `_validate_enrichment()` function that runs after enrichment:

```python
def _validate_enrichment(rows, vendor_dir, prop_dir):
    """Validate enriched data. Returns list of warnings."""
    warnings = []

    if not rows:
        return warnings

    first = rows[0]
    vendor_name = first.get("EnrichedVendorName", "")
    vendor_id = first.get("EnrichedVendorID", "")
    prop_name = first.get("EnrichedPropertyName", "")
    prop_state = first.get("EnrichedPropertyState", "")
    svc_state = first.get("Service State", "")

    # Rule 1: Vendor-property state mismatch
    if svc_state and prop_state and svc_state != prop_state:
        warnings.append({
            "rule": "state_mismatch",
            "message": f"Service state ({svc_state}) ≠ property state ({prop_state})",
            "severity": "high"
        })

    # Rule 2: Account number doesn't match vendor pattern
    acct = first.get("Account Number", "")
    vendor_info = next((v for v in vendor_dir if v["vendor_id"] == vendor_id), None)
    if vendor_info and vendor_info.get("account_number_pattern") and acct:
        import re
        if not re.match(vendor_info["account_number_pattern"], acct):
            warnings.append({
                "rule": "account_pattern_mismatch",
                "message": f"Account '{acct}' doesn't match expected pattern",
                "severity": "medium"
            })

    # Rule 3: Bill date in the future
    bill_date_str = first.get("Bill Date", "")
    if bill_date_str:
        try:
            bd = parse_date(bill_date_str)
            if bd and bd > datetime.date.today():
                warnings.append({
                    "rule": "future_bill_date",
                    "message": f"Bill date {bill_date_str} is in the future",
                    "severity": "high"
                })
        except:
            pass

    # Rule 4: Charge amounts don't sum correctly
    total = sum(float(r.get("Charge Amount", 0) or 0) for r in rows)
    if total == 0:
        warnings.append({
            "rule": "zero_total",
            "message": "All charges sum to zero",
            "severity": "high"
        })

    # Rule 5: Duplicate line items
    seen = set()
    for r in rows:
        desc = r.get("Charge Description", "").strip()
        amt = r.get("Charge Amount", "").strip()
        key = f"{desc}|{amt}"
        if key in seen and desc:
            warnings.append({
                "rule": "duplicate_line",
                "message": f"Duplicate: {desc} ${amt}",
                "severity": "low"
            })
        seen.add(key)

    # Rule 6: Vendor matched with low confidence
    if not vendor_id:
        warnings.append({
            "rule": "no_vendor_match",
            "message": "Could not match vendor to any known vendor",
            "severity": "high"
        })

    # Rule 7: Property matched with low confidence
    if not prop_name:
        warnings.append({
            "rule": "no_property_match",
            "message": "Could not match to any known property",
            "severity": "high"
        })

    return warnings
```

### Storing Validation Results

Write warnings into the JSONL header row:

```python
warnings = _validate_enrichment(enriched_rows, vendor_dir, prop_dir)
if warnings:
    enriched_rows[0]["_validation_warnings"] = json.dumps(warnings)
    # Route high-severity warnings to flagged review
    if any(w["severity"] == "high" for w in warnings):
        dest_prefix = FLAGGED_REVIEW_PREFIX  # Stage 9 instead of Stage 4
```

---

## Step 8: OCR Fallback for Scanned PDFs {#step-8}

**Impact:** LOW-MEDIUM (only affects scanned PDFs)
**Effort:** 3-4 days
**File:** `lambda_bill_parser.py`

### Detection Logic

```python
import fitz  # PyMuPDF

def is_scanned_pdf(pdf_bytes):
    """Check if a PDF is scanned (image-only, no extractable text)."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_text = ""
        for page in doc:
            total_text += page.get_text()
        doc.close()
        # If less than 50 chars of text per page, likely scanned
        chars_per_page = len(total_text.strip()) / max(len(doc), 1)
        return chars_per_page < 50
    except:
        return False
```

### Image-Based Fallback

```python
def convert_pdf_to_images(pdf_bytes, dpi=300):
    """Convert PDF pages to PNG images for Gemini vision."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        images.append(pix.tobytes("png"))
    doc.close()
    return images

def call_gemini_with_images(images, prompt):
    """Send page images to Gemini instead of PDF binary."""
    parts = [{"text": prompt}]
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode()
        parts.append({
            "inline_data": {
                "mime_type": "image/png",
                "data": b64
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }
    # ... standard Gemini REST call ...
```

### Integration

```python
def process_pdf(pdf_bytes, s3_key):
    """Main processing function with OCR fallback."""

    # Try standard PDF processing first
    result = call_gemini_with_retry_rest(pdf_bytes, PROMPT)

    if result and len(result) > 0:
        return result  # Standard processing succeeded

    # Check if scanned PDF
    if is_scanned_pdf(pdf_bytes):
        print(f"[PARSER] Scanned PDF detected, using image mode: {s3_key}")
        images = convert_pdf_to_images(pdf_bytes)
        result = call_gemini_with_images(images, PROMPT)
        if result:
            return result

    # Both failed
    print(f"[PARSER] All extraction methods failed: {s3_key}")
    return []
```

---

## Step 9: Vendor-Specific Parsing Templates {#step-9}

**Impact:** HIGH for top vendors (80/20 rule)
**Effort:** 3-5 days (mostly manual documentation)
**Files:** DynamoDB knowledge base, `lambda_bill_parser.py`

### Concept

For the top 10 vendors by bill volume, create vendor-specific parsing instructions that describe:
- Bill layout and structure
- Where to find key fields
- Expected line items and charge types
- Common gotchas and special cases

### Example: Southern California Edison (SCE)

Store in DynamoDB `jrk-bill-config` table:

```json
{
  "PK": "VENDOR_TEMPLATE",
  "SK": "SCE",
  "vendor_name": "Southern California Edison",
  "match_patterns": ["edison", "sce", "socal edison"],
  "template": "SCE bills have a summary page followed by detail pages.\n\nPage 1 (Summary):\n- Account number in top-right: format D-DDD-DDD-DDDD\n- Service address below account number\n- Bill date and due date in header\n- Total amount due prominently displayed\n\nDetail Pages:\n- Line items listed under 'Details of Your Charges'\n- Main categories: Delivery, Generation, Others\n- Each category has sub-line-items\n- Tax shown separately at bottom\n\nCommon charges:\n- Delivery Service (base charge + per-kWh tiers)\n- Generation (per-kWh)\n- Public Purpose Programs\n- Franchise Fee (percentage of total)\n- Nuclear Decommissioning\n- Competition Transition Charge\n- DWR Bond Charge\n\nGotchas:\n- Multiple meters may appear on one bill — extract ALL meters\n- Rate schedules (TOU-D-A, etc.) appear near usage — ignore these\n- Credits appear as negative amounts\n- Late fees appear as separate line items with 'LATE' in description",
  "account_pattern": "^\\d-\\d{3}-\\d{3}-\\d{4}$",
  "utility_type": "electric"
}
```

### How to Inject at Parse Time

The `fetch_knowledge_notes()` function already exists in the parser. Extend it:

```python
def get_vendor_template(vendor_hint):
    """Look up vendor-specific parsing template from DynamoDB."""
    if not vendor_hint:
        return ""

    # Try direct match first
    try:
        resp = ddb.get_item(
            TableName=CONFIG_TABLE,
            Key={"PK": {"S": "VENDOR_TEMPLATE"}, "SK": {"S": vendor_hint.upper()}}
        )
        if "Item" in resp:
            return resp["Item"]["template"]["S"]
    except:
        pass

    # Try pattern matching
    try:
        resp = ddb.scan(
            TableName=CONFIG_TABLE,
            FilterExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": "VENDOR_TEMPLATE"}}
        )
        vendor_lower = vendor_hint.lower()
        for item in resp.get("Items", []):
            patterns = json.loads(item.get("match_patterns", {}).get("S", "[]"))
            if any(p in vendor_lower for p in patterns):
                return item["template"]["S"]
    except:
        pass

    return ""
```

### Top 10 Vendors to Template

Based on the scraper API data, these vendors likely have the highest volume:

1. **Tacoma Public Utilities** (3,400 bills) — water/electric/sewer combined
2. **Ameren** (791 bills) — electric, IL/MO
3. **Southern California Edison** — electric, CA
4. **Pacific Gas & Electric** — gas/electric, CA
5. **San Diego Gas & Electric** — gas/electric, CA
6. **LADWP** — water/power, CA
7. **Peoples Gas** — gas, IL
8. **ComEd** — electric, IL
9. **Xcel Energy** — electric/gas, CO/MN
10. **Duke Energy** — electric, NC/SC

Each template takes 30-60 minutes of manual documentation by someone familiar with the bills.

---

## Step 10: Enrichment Cache Unification {#step-10}

**Impact:** MEDIUM — eliminates inconsistency between Lambda and app
**Effort:** 2-3 days
**Files:** `lambda_bill_enricher.py`, `main.py`, `build_vendor_cache.py`

### The Problem

The enricher Lambda and the main.py app each maintain separate vendor/property caches:

| Cache | Where | Structure | Refresh |
|-------|-------|-----------|---------|
| `dim_vendor/*.json` | S3 | Enricher format | vendor-cache-builder Lambda |
| `_POST_HELPER_CACHE` | main.py memory | App format | 5-min TTL |
| `_VENDOR_PAIR_CACHE` | main.py | Historical vendor-GL pairs | On demand |
| `vendor_directory.json` | S3 (proposed) | Extended format | Step 4 builder |
| `property_directory.json` | S3 (proposed) | Extended format | Step 2 builder |

Different caches mean the enricher might match to one vendor/property while the app's review page shows a different candidate list.

### Solution

Create a single `reference_bundle.json` in S3 that both Lambda and AppRunner read:

```json
{
  "built_at": "2026-03-19T12:00:00Z",
  "vendors": [...],      // from vendor-cache-builder + alternate names
  "properties": [...],   // from property-directory-builder
  "gl_accounts": [...],  // from dim_gl_account
  "vendor_templates": [...],  // from DynamoDB VENDOR_TEMPLATE
  "version": 42
}
```

Both the enricher and main.py load this same file. A version number enables cache invalidation.

---

## Implementation Timeline

### Phase 1: Foundation (Weeks 1-2)
- Step 1: JSON mode migration (eliminate column shifts)
- Step 5: Header normalization (quick win)

### Phase 2: Reference Data (Weeks 2-4)
- Step 2: Property reference directory
- Step 4: Vendor alternate names
- Step 3: Reference data injection into parser prompt

### Phase 3: Validation & Confidence (Weeks 4-6)
- Step 6: Per-field confidence scoring
- Step 7: Post-enrichment validation rules
- Step 8: OCR fallback

### Phase 4: Vendor Optimization (Weeks 6-8)
- Step 9: Top 10 vendor templates
- Step 10: Cache unification

### Expected Accuracy Improvement

| Metric | Current (est.) | After Phase 1 | After Phase 2 | After All |
|--------|---------------|---------------|---------------|-----------|
| Vendor match accuracy | ~80% | ~82% | ~95% | ~98% |
| Property match accuracy | ~75% | ~77% | ~92% | ~96% |
| Line item extraction | ~85% | ~93% | ~95% | ~97% |
| GL assignment accuracy | ~78% | ~80% | ~88% | ~94% |
| Bills needing manual review | ~40% | ~30% | ~15% | ~8% |
