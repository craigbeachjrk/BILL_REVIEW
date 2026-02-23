# Bill Review System

A web application for processing utility bills from PDF to Entrata posting.

## What It Does

**Automates utility bill processing:** PDFs are uploaded, parsed by AI, enriched with property/vendor/GL data, reviewed by staff, and posted to Entrata.

## Pipeline Stages

```
PDF Upload → Parse → Enrich → Review → POST → BILLBACK → Track
   (S3)      (AI)    (Auto)   (Human)  (Queue) (Entrata)  (Done)
```

| Stage | What Happens |
|-------|--------------|
| **1. Upload** | PDFs dropped in S3 inbox |
| **2. Parse** | Lambda extracts line items via Claude AI |
| **3. Enrich** | Auto-matches property, vendor, GL accounts |
| **4. Review** | Staff reviews/edits in web UI |
| **5. POST** | Approved bills queued for posting |
| **6. BILLBACK** | Staff posts to Entrata with charge codes |
| **7. Track** | Completed bills archived |

## Key Features

- **AI Parsing** - Claude extracts data from any utility bill format
- **Auto-Enrichment** - Matches accounts to properties/vendors/GL codes
- **Bulk Operations** - Assign property/vendor to multiple invoices at once
- **Charge Code Mapping** - GL codes map to Entrata charge codes by property
- **UBI Integration** - Generates master bills for resident billing
- **Role-Based Access** - System Admins, AP Team, Viewers

## Pages

| Page | Purpose |
|------|---------|
| **Review** | Edit parsed bill data before approval |
| **Invoices** | Browse all invoices, bulk assign, send to rework |
| **POST** | Queue of approved bills ready to post |
| **BILLBACK** | Post bills to Entrata |
| **Track** | View completed/posted bills |
| **Settings** | Configure mappings, charge codes, users |

## Tech Stack

- **Backend:** FastAPI (Python)
- **Frontend:** Jinja2 templates, vanilla JS
- **Storage:** S3 (bills), DynamoDB (drafts/config)
- **Parsing:** AWS Lambda + Claude AI
- **Hosting:** AWS AppRunner
