#!/usr/bin/env python3
"""
Populate the DEBUG table with enhancement recommendations from codebase analysis.

This script creates bug/enhancement reports in the jrk-bill-review-debug DynamoDB table
based on the comprehensive codebase analysis completed on 2025-11-07.
"""

import boto3
import uuid
from datetime import datetime, timezone

# AWS Configuration
PROFILE = "jrk-analytics-admin"
REGION = "us-east-1"
DEBUG_TABLE = "jrk-bill-review-debug"

# Initialize DynamoDB client
session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ddb = session.client("dynamodb", region_name=REGION)

def create_report(title: str, description: str, priority: str, page_url: str = ""):
    """Create a debug report in DynamoDB."""
    report_id = str(uuid.uuid4())
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    item = {
        "report_id": {"S": report_id},
        "title": {"S": title},
        "description": {"S": description},
        "page_url": {"S": page_url},
        "requestor": {"S": "System Analysis"},
        "status": {"S": "Open"},
        "priority": {"S": priority},
        "created_utc": {"S": now_utc},
        "updated_utc": {"S": now_utc},
    }

    try:
        ddb.put_item(TableName=DEBUG_TABLE, Item=item)
        print(f"[OK] Created [{priority}] {title}")
        return True
    except Exception as e:
        print(f"[FAIL] Failed to create report: {e}")
        return False

# Enhancement Reports from Codebase Analysis
REPORTS = [
    # CRITICAL SECURITY ISSUES
    {
        "title": "Implement CSRF Protection",
        "description": """All POST endpoints in main.py lack CSRF protection, making the application vulnerable to cross-site request forgery attacks.

**Implementation:**
```python
from starlette.middleware.csrf import CSRFMiddleware
app.add_middleware(CSRFMiddleware, secret=APP_SECRET)
```

**Impact:** Critical security vulnerability
**Location:** main.py - all POST endpoints
**Effort:** 4-6 hours
**Reference:** Issue #2 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Remove Authentication Bypass",
        "description": """Environment variable DISABLE_AUTH allows complete authentication bypass in production.

**Current Code (main.py:150-152):**
```python
if os.getenv("DISABLE_AUTH", "0") == "1":
    return os.getenv("ADMIN_USER", "admin")
```

**Action:** Remove this bypass or restrict to development environments only with explicit warnings.

**Impact:** Critical - allows unauthorized access
**Effort:** 2 hours
**Reference:** Issue #6 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Fix XSS Vulnerabilities in Templates",
        "description": """Multiple templates render user data without proper escaping, creating XSS vulnerabilities.

**Affected Files:**
- review.html
- invoices.html
- debug.html
- Other templates

**Actions:**
1. Ensure Jinja2 autoescape is enabled globally
2. Add |e filter to all user-provided data
3. Implement Content Security Policy headers

**Impact:** Critical - allows malicious script injection
**Effort:** 8 hours
**Reference:** Issue #35 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": "/review"
    },
    {
        "title": "Enforce APP_SECRET Requirement",
        "description": """APP_SECRET has a default value 'dev-secret-change-me' which may be deployed to production.

**Current Code (main.py:76):**
```python
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
```

**Action:** Require APP_SECRET to be set, fail fast if missing in production.

**Impact:** High - session security compromised
**Effort:** 1 hour
**Reference:** Issue #5 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },

    # RELIABILITY & MONITORING
    {
        "title": "Set Up Unit Testing Framework",
        "description": """Application has 0% test coverage, making refactoring risky and bugs likely.

**Actions:**
1. Set up pytest framework
2. Create test fixtures for DynamoDB, S3 mocks
3. Add tests for critical business logic (posting, enrichment, GL assignment)
4. Set up CI/CD to run tests automatically
5. Target: 80%+ coverage

**Impact:** Critical - no safety net for changes
**Effort:** 30 hours for initial setup + ongoing
**Reference:** Issue #46 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Configure Dead Letter Queues for All Lambdas",
        "description": """Failed Lambda invocations are lost permanently, making debugging impossible.

**Actions:**
1. Create SQS DLQ for each Lambda function
2. Configure Lambda to send failures to DLQ
3. Set up CloudWatch alarm when DLQ not empty
4. Create Lambda to process/replay DLQ messages

**Affected Lambdas:**
- jrk-bill-parser
- jrk-bill-enricher
- jrk-bill-router
- jrk-bill-chunk-processor

**Impact:** High - data loss on failures
**Effort:** 3 hours
**Reference:** Issue #45, see INFRASTRUCTURE_RECOMMENDATIONS.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Add CloudWatch Alarms",
        "description": """No monitoring for system health - failures go unnoticed until users report.

**Alarms Needed:**
1. Lambda error rate > 5%
2. Lambda duration approaching timeout
3. Lambda throttling
4. DynamoDB throttling
5. S3 4xx errors
6. Daily cost anomalies
7. Parser success rate < 90%

**Impact:** High - operational blindness
**Effort:** 4 hours
**Cost:** $1.50/month for alarms
**Reference:** See INFRASTRUCTURE_RECOMMENDATIONS.md for complete setup""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Create DynamoDB Indexes for Performance",
        "description": """Missing Global Secondary Indexes cause slow queries and high costs.

**Indexes Needed:**

**jrk-bill-review-drafts:**
1. status-updated-index (status + updated_utc)
2. user-date-index (user + parsed_date)
3. property-vendor-index (property_id + vendor_id)

**jrk-bill-review-debug:**
1. status-created-index (verify exists)
2. priority-status-index (priority + status)

**Impact:** High - 10-100x query performance improvement
**Effort:** 2 hours
**Cost Savings:** 50-70% reduction in read capacity
**Reference:** See INFRASTRUCTURE_RECOMMENDATIONS.md""",
        "priority": "High",
        "page_url": ""
    },

    # PERFORMANCE OPTIMIZATIONS
    {
        "title": "Optimize S3 Pagination in list_dates()",
        "description": """list_dates() iterates through ALL objects in bucket every time, causing slow page loads.

**Current Issue (main.py:180-196):**
Paginator scans entire bucket with no limits or filtering.

**Solutions:**
1. Implement date-based prefix filtering
2. Maintain date index in DynamoDB
3. Use S3 Select for filtering
4. Cache results (with distributed cache)

**Impact:** High - page loads can take 5-10 seconds
**Effort:** 6 hours
**Performance Gain:** 80%+ reduction in S3 API calls
**Reference:** Issue #7 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": "/"
    },
    {
        "title": "Implement Distributed Caching with Redis",
        "description": """Current in-memory cache doesn't work with multiple App Runner instances.

**Current Issue (main.py:117-119):**
```python
_CACHE: dict = {}  # Won't work with auto-scaling
```

**Actions:**
1. Set up ElastiCache Redis cluster
2. Implement cache wrapper for:
   - Vendor/property candidates
   - Configuration data
   - Frequently accessed S3 objects
3. Add cache invalidation strategy

**Impact:** Medium - inconsistent caching, scalability issues
**Effort:** 12 hours
**Cost:** ~$15-30/month for Redis
**Performance Gain:** 70%+ cache hit rate
**Reference:** Issue #9 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": ""
    },
    {
        "title": "Convert S3 Operations to Async",
        "description": """Synchronous S3 calls block request threads, causing slow response times.

**Current Issue:**
S3 get_object calls in request handlers block the entire request.

**Actions:**
1. Install aioboto3
2. Convert endpoints to async def
3. Use await for S3 operations
4. Test concurrent request handling

**Affected Endpoints:**
- /review, /invoices, /api/post_to_entrata, others

**Impact:** Medium - slow page loads under load
**Effort:** 10 hours
**Performance Gain:** 30%+ faster response times
**Reference:** Issue #8 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": "/review"
    },

    # CODE QUALITY IMPROVEMENTS
    {
        "title": "Refactor api_post_to_entrata() Function",
        "description": """api_post_to_entrata() is 177 lines handling multiple responsibilities.

**Current State (main.py:347-523):**
Single function handles validation, loading, transformation, API calls, and S3 operations.

**Refactor to:**
```python
def validate_post_request(keys, overrides) -> ValidationResult
def load_and_transform_records(keys, overrides) -> List[Record]
def post_to_entrata(records) -> PostResult
def archive_posted_records(keys, results) -> None
```

**Impact:** Medium - hard to test and maintain
**Effort:** 8 hours
**Benefit:** Easier testing, better error handling
**Reference:** Issue #11 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": "/post"
    },
    {
        "title": "Replace Code Duplication with utils.py",
        "description": """Field extraction logic duplicated throughout main.py.

**Current Pattern (repeated 15+ times):**
```python
pid = str(rows[0].get("EnrichedPropertyID") or rows[0].get("Property Id") or ...).strip()
```

**Action:** Use new utils.py module:
```python
from bill_review_app.utils import get_field, parse_amount, validate_required_fields

pid = get_field(rows[0], "EnrichedPropertyID", "Property Id", "PropertyID")
amount = parse_amount(row.get("amount"))
```

**Impact:** Medium - code duplication, maintenance burden
**Effort:** 4 hours to refactor existing code
**Benefit:** DRY principle, easier maintenance
**Reference:** Issue #15, utils.py already created""",
        "priority": "Low",
        "page_url": ""
    },
    {
        "title": "Add Comprehensive Type Hints",
        "description": """Many functions lack type hints, making code harder to understand and maintain.

**Actions:**
1. Add type hints to all function signatures
2. Use typing module (List, Dict, Optional, Union)
3. Run mypy for type checking
4. Add to CI/CD pipeline

**Example:**
```python
def get_field(record: dict, *field_names: str, default: str = "") -> str:
    ...
```

**Impact:** Low - but improves developer experience
**Effort:** 6 hours
**Benefit:** Better IDE support, fewer bugs
**Reference:** Issue #13 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Low",
        "page_url": ""
    },
    {
        "title": "Implement Pydantic Models for Request Validation",
        "description": """Request parameters validated manually with inconsistent patterns.

**Current State:**
Manual validation throughout API endpoints.

**Use Pydantic:**
```python
from pydantic import BaseModel

class PostToEntrataRequest(BaseModel):
    keys: List[str]
    vendor_overrides: Optional[Dict[str, str]] = None
    post_month: Optional[str] = None

@app.post("/api/post_to_entrata")
async def api_post_to_entrata(request: PostToEntrataRequest):
    # Auto-validated, type-safe
```

**Impact:** Medium - inconsistent validation, security risk
**Effort:** 8 hours
**Benefit:** Automatic validation, better error messages
**Reference:** Issue #17 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": ""
    },
    {
        "title": "Add Structured Logging with Correlation IDs",
        "description": """Minimal logging makes debugging difficult. Add structured logging throughout.

**Actions:**
1. Install structlog
2. Add correlation IDs to requests
3. Log all operations with context
4. Send logs to CloudWatch Logs Insights

**Example:**
```python
import structlog
logger = structlog.get_logger()

logger.info("processing_post",
    user=user,
    keys_count=len(keys),
    correlation_id=request_id)
```

**Impact:** Medium - debugging is painful
**Effort:** 10 hours
**Benefit:** Faster debugging, better observability
**Reference:** Issue #16 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": ""
    },

    # LAMBDA IMPROVEMENTS
    {
        "title": "Implement Circuit Breaker for Gemini API",
        "description": """No protection against cascading failures if Gemini API is down.

**Current State (lambda_bill_enricher.py:560-598):**
Direct API calls without failure protection.

**Action:** Implement circuit breaker pattern:
```python
from pybreaker import CircuitBreaker

gemini_breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=60
)

@gemini_breaker
def call_gemini_api(payload):
    ...
```

**Impact:** High - cascading failures
**Effort:** 4 hours
**Benefit:** Graceful degradation, faster failure recovery
**Reference:** Issue #28 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Add Lambda Idempotency Checks",
        "description": """Lambdas can be invoked multiple times for same S3 event causing duplicate processing.

**Current State:**
No check if file already processed.

**Actions:**
1. Check if output file exists before processing
2. Use DynamoDB to track processed files
3. Use S3 event ID for deduplication

**Affected Lambdas:**
- lambda_bill_enricher.py (line 863-881)
- All other processing Lambdas

**Impact:** High - duplicate processing, incorrect data
**Effort:** 6 hours
**Reference:** Issue #29 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Extract GL Assignment Logic to Rules Engine",
        "description": """300+ lines of GL assignment logic embedded in Lambda - hard to test and maintain.

**Current State (lambda_bill_enricher.py:486-792):**
Complex business rules in Lambda function.

**Actions:**
1. Extract rules to separate module
2. Use rule engine pattern (JSON/YAML configs)
3. Make rules editable via admin UI
4. Add comprehensive tests

**Impact:** High - business logic changes require Lambda deploy
**Effort:** 20 hours
**Benefit:** Faster rule changes, better testing
**Reference:** Issue #26 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": ""
    },
    {
        "title": "Implement Exponential Backoff for API Retries",
        "description": """Fixed retry logic doesn't account for rate limits or transient failures.

**Current State (lambda_bill_parser.py:61):**
```python
MAX_ATTEMPTS = 10  # Simple counter
```

**Action:** Implement exponential backoff with jitter:
```python
import time
import random

def retry_with_backoff(func, max_attempts=5):
    for attempt in range(max_attempts):
        try:
            return func()
        except RateLimitError:
            if attempt == max_attempts - 1:
                raise
            wait_time = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(wait_time)
```

**Impact:** Medium - API failures, rate limit issues
**Effort:** 3 hours
**Reference:** Issue #19 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": ""
    },

    # INFRASTRUCTURE & COST
    {
        "title": "Implement S3 Lifecycle Policies",
        "description": """Historical data accumulates indefinitely with no archival strategy.

**Actions:**
1. Move parsed PDFs to Glacier after 90 days
2. Expire tmp/ files after 7 days
3. Archive enriched data after 180 days

**Implementation:**
See INFRASTRUCTURE_RECOMMENDATIONS.md for complete lifecycle.json

**Impact:** Medium - increasing costs
**Effort:** 1 hour
**Cost Savings:** $100-500/month
**Reference:** Issue #43, see INFRASTRUCTURE_RECOMMENDATIONS.md""",
        "priority": "Medium",
        "page_url": ""
    },
    {
        "title": "Add Content Security Policy Headers",
        "description": """Large inline JavaScript blocks violate Content Security Policy.

**Current State:**
All templates have inline JavaScript, no CSP headers.

**Actions:**
1. Move JavaScript to external files
2. Add CSP middleware to FastAPI
3. Use nonce or hash-based CSP

**Example:**
```python
from starlette.middleware.trustedhost import TrustedHostMiddleware
app.add_middleware(
    CSPMiddleware,
    policy="default-src 'self'; script-src 'self' 'nonce-{nonce}'"
)
```

**Impact:** High - XSS protection
**Effort:** 12 hours
**Reference:** Issue #36 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Add Subresource Integrity to External Resources",
        "description": """External CDN resources loaded without integrity checking.

**Current State (review.html:7-11):**
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/..." />
```

**Action:** Add SRI hashes:
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/choices.js/..."
      integrity="sha384-xxx" crossorigin="anonymous" />
```

**Impact:** Medium - CDN compromise risk
**Effort:** 1 hour
**Reference:** Issue #38 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Medium",
        "page_url": "/review"
    },
    {
        "title": "Improve Accessibility (ARIA Labels, Keyboard Nav)",
        "description": """Templates missing ARIA labels and keyboard navigation support.

**Current Issues:**
- No ARIA labels on interactive elements
- Insufficient keyboard navigation
- Screen reader support lacking

**Actions:**
1. Add ARIA labels to all forms and interactive elements
2. Ensure all functionality keyboard-accessible
3. Add skip navigation links
4. Test with screen readers
5. Add accessibility testing to CI/CD

**Impact:** Medium - compliance, user experience
**Effort:** 8 hours
**Benefit:** ADA compliance, better UX for all users
**Reference:** Issue #40 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Low",
        "page_url": ""
    },
    {
        "title": "Set Up CI/CD Pipeline",
        "description": """No automated testing, linting, or deployment validation.

**Actions:**
1. Set up GitHub Actions or CodePipeline
2. Add automated stages:
   - Linting (ruff, mypy)
   - Unit tests (pytest)
   - Security scanning (bandit, safety)
   - Integration tests
   - Automated deployment to staging

**Benefits:**
- Catch bugs before production
- Consistent code quality
- Automated deployments
- Audit trail

**Impact:** High - quality assurance
**Effort:** 16 hours for initial setup
**Reference:** Issue #47 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "High",
        "page_url": ""
    },
    {
        "title": "Add API Documentation with OpenAPI/Swagger",
        "description": """No API documentation makes integration difficult.

**Actions:**
1. Use FastAPI automatic documentation
2. Add detailed descriptions to endpoints
3. Add request/response examples
4. Document error codes
5. Enable /docs and /redoc endpoints

**Example:**
```python
@app.post("/api/post_to_entrata",
    summary="Post invoices to Entrata",
    description="Post validated invoices to Entrata system...")
async def api_post_to_entrata(...):
```

**Impact:** Medium - developer experience
**Effort:** 4 hours
**Benefit:** Self-documenting API
**Reference:** Issue #49 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Low",
        "page_url": ""
    },
    {
        "title": "Implement Configuration Management Class",
        "description": """Configuration scattered throughout code with hard-coded defaults.

**Current State (main.py:70-102):**
Multiple os.getenv() calls with defaults.

**Use Pydantic Settings:**
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    bucket: str
    aws_region: str = "us-east-1"
    app_secret: str
    disable_auth: bool = False

    class Config:
        env_prefix = ""
        case_sensitive = False

settings = Settings()
```

**Impact:** Medium - maintainability
**Effort:** 3 hours
**Benefit:** Type-safe configuration, validation
**Reference:** Issue #14 in CODEBASE_ANALYSIS_REPORT.md""",
        "priority": "Low",
        "page_url": ""
    },
]

def main():
    """Populate all enhancement reports."""
    print(f"Populating {len(REPORTS)} reports into {DEBUG_TABLE}...")
    print()

    success_count = 0
    fail_count = 0

    for report in REPORTS:
        if create_report(**report):
            success_count += 1
        else:
            fail_count += 1

    print()
    print(f"[SUCCESS] Successfully created {success_count} reports")
    if fail_count > 0:
        print(f"[ERROR] Failed to create {fail_count} reports")
    print()
    print(f"View reports at: https://your-app-url.com/debug")
    print(f"Or query DynamoDB table: {DEBUG_TABLE}")

if __name__ == "__main__":
    main()
