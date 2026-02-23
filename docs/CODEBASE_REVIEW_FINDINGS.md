# Codebase Review Findings

**Reviewed:** 2025-01-19
**Reviewer:** Claude Code (4 parallel review agents)
**Scope:** main.py, templates/, aws_lambdas/

---

## SECURITY ISSUES

### High Priority

1. **S3 Key Path Traversal** - Several endpoints don't validate S3 keys
   - `_validate_s3_key()` exists but not consistently used
   - Affected: flagged endpoints, some draft endpoints
   - Fix: Apply validation to all user-provided S3 keys

2. **SQL Injection in Snowflake Queries** - String interpolation used
   - Location: Snowflake query endpoints
   - Fix: Use parameterized queries

3. **Error Messages Expose Internals** - Stack traces in some responses
   - `_sanitize_error()` exists but not always used
   - Fix: Consistent error sanitization

### Medium Priority

4. **Session Cookie Settings** - Missing secure/httponly flags in some paths
5. **Missing CSRF Protection** - Forms use POST but no CSRF tokens
6. **Admin Check Inconsistency** - Some endpoints missing `ADMIN_USERS` check

---

## BACKEND BUGS (main.py)

### Data Integrity

1. **Unused `now_utc` Variable** - Assigned but never used in `/api/flagged/unflag` (line 3933)
   - Actually used at line 3969 - FALSE POSITIVE

2. **Concurrent S3 Write Risk** - Multiple users editing same file
   - No locking mechanism on S3 JSONL files
   - Fix: Consider optimistic locking or DynamoDB for coordination

3. **Cache Invalidation Gaps** - Not all mutation endpoints invalidate cache
   - `invalidate_day_cache()` should be called after all S3 writes

4. **Empty File Handling** - Some endpoints don't handle empty JSONL files

### API Issues

5. **Inconsistent Response Formats**
   - Some return `{"ok": True, ...}`, others return `{"success": true, ...}`
   - Some errors return `{"error": "msg"}`, others `{"detail": "msg"}`
   - Fix: Standardize on single format

6. **Missing Pagination** - Large result sets returned without limits
   - `/api/flagged` - returns all items
   - `/api/catalog/vendors` - returns all vendors
   - Fix: Add offset/limit parameters

7. **Timeout Risks** - Sequential S3 operations in loops
   - `/api/flagged` loops 90 days sequentially
   - Fix: Parallel fetch with ThreadPoolExecutor

---

## TEMPLATE ISSUES

### JavaScript Bugs

1. **Missing Error Handlers** - Some async calls lack try/catch
2. **Memory Leaks** - Event listeners not cleaned up on navigation
3. **Variable Shadowing** - `line` variable reused in nested loops

### UI/UX Issues

4. **Z-index Stacking** - Some modals use z-index: 10000, headers use 100
5. **Responsive Breakpoints** - Tables overflow on mobile
6. **Accessibility** - Missing ARIA labels, focus management

### Security (XSS)

7. **Unescaped Output** - Some template variables rendered without escaping
   - `${hash}` in data attributes
   - User-submitted content in titles
   - Fix: Always use escapeHtml() for user data

---

## LAMBDA ISSUES

### Bill Parser (jrk-bill-parser)

1. **Regex Pattern Issues** - Some patterns may miss edge cases
   - Amount parsing: `$1,234.56` vs `1234.56` vs `(1,234.56)`

2. **PDF Text Extraction** - Silent failures on corrupted PDFs

3. **Memory Usage** - Large PDFs loaded fully into memory

### Bill Router (jrk-bill-router)

4. **Page Count Errors** - PyPDF2 throws on malformed PDFs
   - No try/catch around page count

### Bill Enricher (jrk-bill-enricher)

5. **Fuzzy Match Threshold** - 80% may be too lenient
6. **Missing GL Codes** - Falls back to generic "Unknown"

---

## ARCHITECTURAL CONCERNS

### Technical Debt

1. **File Size** - main.py is ~4200 lines, should be split
   - Suggested modules: auth.py, api_invoices.py, api_ubi.py, api_flagged.py

2. **Duplicate Code** - S3 read/write patterns repeated
   - Extract to utility functions

3. **Magic Numbers** - Hardcoded values throughout
   - `days_back=90`, timeouts, thresholds
   - Fix: Move to config/constants

4. **Import Organization** - Some imports inside functions
   - `from datetime import datetime` repeated in multiple endpoints

### Performance

5. **N+1 Query Pattern** - Loop over S3 keys, fetch each
   - Use batch operations where possible

6. **Missing Caching** - Some expensive operations not cached
   - Vendor/property lists fetched repeatedly

7. **Sync Operations in Async Endpoints** - Blocking S3 calls in async functions

---

## TESTING GAPS

1. **No Unit Tests** - 0% coverage currently
2. **No Integration Tests** - API endpoints untested
3. **No Mocking** - Would need moto for AWS services

See `tests/` plan in separate document for test coverage strategy.

---

## RECOMMENDED PRIORITY ORDER

### Immediate (Security)
1. S3 key validation on all endpoints
2. Error sanitization consistency
3. Admin check on `/api/billback/flag`

### Short-term (Bugs)
4. Fix race condition in `/api/flagged/confirm`
5. Standardize response formats
6. Add pagination to large endpoints

### Medium-term (Tech Debt)
7. Split main.py into modules
8. Add comprehensive tests
9. Implement proper caching strategy

### Long-term (Architecture)
10. Add DynamoDB locking for concurrent edits
11. Move to async S3 operations
12. Add monitoring/alerting
