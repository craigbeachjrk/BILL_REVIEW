# 02 — Endpoint Inventory

Complete list of all FastAPI routes in `main.py`. Status as of 2026-04-16.

**Count:** 349 endpoints across ~14 logical modules. All require authentication (`Depends(require_user)`) except `/login`, `/logout`, and `/health`. Endpoints marked "Admin" in the Auth column require the admin role via `require_admin`.

**Sort order:** Roughly by module grouping. Module groupings align with `03_module_taxonomy.md`.

**How to use:**
- Use Cmd+F with URL path to find an endpoint's line number
- Line numbers are in `main.py`
- "Purpose" is a one-line summary extracted from route/function context — see module docs for full behavior

---

## Endpoint Table

| Method | Path | Line | Auth | Module | Purpose |
|--||||--||
| GET | / | 3430 | User | Core | Home dashboard |
| GET | /login | 3169 | No | Auth | Login form |
| POST | /login | 3174 | No | Auth | Process login |
| POST | /logout | 3424 | No | Auth | Clear session and logout |
| GET | /change-password | 3219 | User | Auth | Change password form |
| POST | /change-password | 3225 | User | Auth | Process password change |
| GET | /health | 3418 | No | Core | AppRunner health check |
| GET | /config/users | 3266 | Admin | Auth | User management page |
| GET | /api/users | 3285 | Admin | Auth | List all users |
| POST | /api/users | 3296 | Admin | Auth | Create new user |
| POST | /api/users/{user_id}/disable | 3328 | Admin | Auth | Disable user |
| POST | /api/users/{user_id}/enable | 3341 | Admin | Auth | Enable user |
| POST | /api/users/{user_id}/reset-password | 3354 | Admin | Auth | Reset user password |
| POST | /api/users/{user_id}/role | 3386 | Admin | Auth | Change user role |
| GET | /parse | 3439 | User | Parse | Parse dashboard with pagination |
| GET | /input | 3512 | User | Parse | Input upload view |
| GET | /search | 3517 | User | Search | Global search page |
| GET | /api/search | 3789 | User | Search | Execute search query |
| GET | /api/search/index-status | 3851 | User | Search | Check search index status |
| POST | /api/search/rebuild-index | 3864 | User | Search | Force search index rebuild |
| POST | /api/upload_input | 3879 | User | Parse | Upload PDF to Stage 1 |
| POST | /api/retrigger_pending_pdfs | 3908 | User | Parse | Retrigger uppercase .PDF files |
| GET | /api/scraper/providers | 4085 | User | Scraper | List utility scraper providers |
| GET | /api/scraper/accounts/{provider_folder} | 4171 | User | Scraper | List accounts for provider |
| GET | /api/scraper/pdfs/{provider_folder}/{account_folder:path} | 4256 | User | Scraper | List PDFs for account |
| GET | /api/scraper/all-pdfs/{provider_folder} | 4311 | User | Scraper | List all PDFs for provider |
| POST | /api/scraper/import | 4411 | User | Scraper | Import PDFs to bill parser |
| POST | /api/scraper/extract-dates | 4690 | User | Scraper | Extract dates using Gemini AI |
| POST | /api/scraper/save-dates | 4712 | User | Scraper | Save extracted dates to cache |
| POST | /api/scraper/get-cached-dates | 4738 | User | Scraper | Get cached service dates |
| GET | /post | 4759 | User | Post | Merged files ready for Entrata |
| GET | /api/post/total | 4853 | User | Post | Lazy-load total for POST file |
| POST | /api/post/validate | 2088 | User | Post | Validate invoices before posting |
| POST | /api/clear_post_locks | 2359 | User | Post | Force-clear posting locks |
| GET | /api/test_post_lock | 2385 | User | Post | Test post lock mechanism |
| POST | /api/verify_entrata_sync | 2500 | User | Post | Verify Entrata sync status |
| POST | /api/post_to_entrata | 2648 | User | Post | Submit to Entrata API |
| POST | /api/advance_to_post_stage | 3002 | User | Post | Move to Post-Entrata stage |
| POST | /api/archive_parsed | 3048 | User | Post | Archive to historical storage |
| GET | /ubi | 4887 | User | UBI | UBI classification page |
| GET | /api/billback/ubi/unassigned | 5432 | User | UBI | Get unassigned bills |
| GET | /api/billback/ubi/filter-options | 5351 | User | UBI | Get filter options |
| POST | /api/billback/ubi/assign | 5536 | User | UBI | Assign bill to UBI |
| GET | /api/billback/ubi/suggestions | 5821 | User | UBI | AI-generated UBI suggestions |
| POST | /api/billback/ubi/accept-suggestion | 6066 | User | UBI | Accept AI suggestion |
| GET | /api/billback/ubi/account-history/{account_key:path} | 6291 | User | UBI | Get account history |
| POST | /api/billback/ubi/calculate-suggestion | 6329 | User | UBI | Calculate suggestion manually |
| GET | /api/billback/ubi/assigned | 6372 | User | UBI | Get assigned bills |
| POST | /api/billback/ubi/unassign | 6568 | User | UBI | Unassign bill from UBI |
| POST | /api/billback/ubi/unassign-account | 6749 | User | UBI | Unassign entire account |
| POST | /api/billback/ubi/cleanup-exclusions | 6936 | User | UBI | Clean up exclusion hashes |
| POST | /api/billback/ubi/reassign-account | 7024 | User | UBI | Reassign account to different UBI |
| POST | /api/billback/ubi/reassign | 7158 | User | UBI | Reassign individual bill |
| POST | /api/billback/ubi/archive | 7303 | User | UBI | Archive UBI-assigned items |
| GET | /billback | 4900 | User | Billback | Billback management page |
| GET | /billback/summary | 4906 | User | Billback | Billback summary by property/vendor |
| GET | /api/billback/summary | 5270 | User | Billback | Get billback summary data |
| GET | /api/billback/posted | 5111 | User | Billback | List posted billback invoices |
| POST | /api/billback/save | 5185 | User | Billback | Save billback edits |
| POST | /api/billback/submit | 5241 | User | Billback | Submit billback to tenant |
| POST | /api/billback/archive | 5013 | User | Billback | Archive billback records |
| POST | /api/billback/update-line-item | 19516 | User | Billback | Update line item amount |
| POST | /api/billback/assign-periods | 19594 | User | Billback | Assign to periods |
| POST | /api/billback/send-to-post | 19645 | User | Billback | Send to post stage |
| POST | /api/billback/flag | 7459 | User | Flagged | Flag record for review |
| GET | /flagged | 7450 | User | Flagged | Flagged review dashboard |
| GET | /api/flagged | 7591 | User | Flagged | Get flagged records |
| POST | /api/flagged/unflag | 7680 | User | Flagged | Remove flag from record |
| POST | /api/flagged/generate-email | 7783 | User | Flagged | Generate email for flagged |
| POST | /api/flagged/confirm | 7923 | User | Flagged | Confirm flagged record |
| GET | /api/flagged/stats | 8043 | User | Flagged | Get flagged statistics |
| GET | /api/ubi/stats/by_property | 8128 | User | Metrics | UBI stats by property |
| GET | /master-bills | 4912 | User | Master Bills | Master bills management |
| POST | /api/master-bills/generate | 19734 | User | Master Bills | Generate master bills |
| GET | /api/master-bills/list | 20202 | User | Master Bills | List master bills |
| GET | /api/master-bills/manual-template | 20347 | User | Master Bills | Get manual entry template |
| GET | /api/master-bills/detail | 20482 | User | Master Bills | Get master bill detail |
| GET | /api/master-bills/diagnose | 20558 | User | Master Bills | Diagnose master bill issues |
| POST | /api/master-bills/exclude-line | 20755 | User | Master Bills | Exclude line from master bill |
| POST | /api/master-bills/reclassify | 20811 | User | Master Bills | Reclassify GL code |
| POST | /api/master-bills/override-amount | 20912 | User | Master Bills | Override line amount |
| POST | /api/master-bills/upload-manual | 21038 | User | Master Bills | Upload manual entry |
| GET | /api/master-bills/manual-entries | 21232 | User | Master Bills | Get manual entries |
| DELETE | /api/master-bills/manual-entry/{entry_id} | 21278 | User | Master Bills | Delete manual entry |
| DELETE | /api/master-bills/manual-batch/{batch_id} | 21305 | User | Master Bills | Delete manual batch |
| GET | /api/master-bills/completion-tracker | 21338 | User | Master Bills | Master bills completion status |
| GET | /api/accrual/calculate | 21955 | User | Accrual | Calculate accrual |
| GET | /api/accrual/cache-stats | 22075 | User | Accrual | Get accrual cache stats |
| GET | /api/accrual/refresh-cache | 22104 | User | Accrual | Refresh accrual cache |
| POST | /api/accrual/create | 22118 | User | Accrual | Create accrual entry |
| DELETE | /api/accrual/entry/{entry_id} | 22176 | User | Accrual | Delete accrual entry |
| GET | /api/accrual/entries | 22206 | User | Accrual | Get accrual entries |
| GET | /ubi-batch | 4918 | User | UBI Batch | UBI batch submission |
| POST | /api/ubi-batch/create | 22276 | User | UBI Batch | Create batch |
| POST | /api/ubi-batch/finalize | 22365 | User | UBI Batch | Finalize batch |
| POST | /api/ubi-batch/delete | 22406 | User | UBI Batch | Delete batch |
| GET | /api/ubi-batch/list | 22450 | User | UBI Batch | List batches |
| GET | /api/ubi-batch/detail/{batch_id} | 22460 | User | UBI Batch | Get batch detail |
| POST | /api/ubi-batch/export-snowflake | 22484 | User | UBI Batch | Export to Snowflake |
| GET | /history | 4926 | User | History | Historical archive view |
| GET | /api/history/archived | 4934 | User | History | Get archived records |
| GET | /billback/charts | 8238 | User | Billback | Billback charts view |
| GET | /config | 8250 | User | Config | Config menu |
| GET | /config/gl-code-mapping | 8268 | User | Config | GL code mapping config |
| GET | /config/account-tracking | 8274 | User | Config | Account tracking config |
| GET | /config/ap-team | 8278 | User | Config | AP team config |
| GET | /config/ap-mapping | 8284 | User | Config | AP mapping config |
| GET | /config/ubi-mapping | 8289 | User | Config | UBI mapping config |
| GET | /config/charge-codes | 8293 | User | Config | Charge codes config |
| GET | /config/post-validation | 8300 | User | Config | Post validation config |
| GET | /api/config/charge-codes | 8305 | User | Config | Get charge codes |
| POST | /api/config/charge-codes | 8322 | User | Config | Save charge codes |
| GET | /config/uom-mapping | 8350 | User | Config | UOM mapping config |
| GET | /config/workflow-reasons | 8427 | User | Config | Workflow reason codes |
| GET | /api/config/workflow-reasons | 8433 | User | Config | Get workflow reasons |
| POST | /api/config/workflow-reasons | 8439 | User | Config | Save workflow reasons |
| GET | /api/workflow/notes | 8466 | User | Workflow | Get workflow notes |
| POST | /api/workflow/notes | 8473 | User | Workflow | Save workflow notes |
| POST | /api/workflow/notes/bulk | 8533 | User | Workflow | Bulk save workflow notes |
| GET | /workflow | 8602 | User | Workflow | Workflow dashboard |
| GET | /workflow/manager | 8609 | User | Workflow | Workflow manager |
| GET | /workflow/manage | 8615 | User | Workflow | Workflow management page |
| GET | /api/workflow | 10449 | User | Workflow | Get workflow data |
| POST | /api/workflow/recalculate | 10433 | User | Workflow | Recalculate workflow metrics |
| POST | /api/workflow/rebuild-bill-index | 10989 | User | Workflow | Rebuild bill index cache |
| GET | /api/workflow/completion-tracker | 11009 | User | Workflow | Get completion tracker |
| GET | /api/workflow/ap-priority | 11184 | User | Workflow | AP priority list |
| GET | /api/workflow/weekly-objectives | 11446 | User | Workflow | Get weekly objectives |
| POST | /api/workflow/weekly-objectives | 11491 | User | Workflow | Save weekly objectives |
| GET | /account-manager | 8642 | User | Account Manager | Account management |
| GET | /api/account-manager/skip-reasons | 8650 | User | Account Manager | Get skip reasons |
| POST | /api/account-manager/rename-account | 8681 | User | Account Manager | Rename account |
| GET | /api/account-manager/rename-history | 8761 | User | Account Manager | Get rename history |
| GET | /api/account-manager/duplicate-bills | 8798 | User | Account Manager | Find duplicate bills |
| GET | /api/account-manager/closed-accounts | 8876 | User | Account Manager | Get closed accounts |
| POST | /api/account-manager/remove-closed-accounts | 8919 | User | Account Manager | Remove closed accounts |
| GET | /directed | 8962 | User | Directed | Directed work dashboard |
| GET | /api/workflow/vacant-accounts | 8968 | User | Workflow | Get vacant property accounts |
| POST | /api/workflow/accounts/archive | 9127 | User | Workflow | Archive account |
| POST | /api/workflow/accounts/restore | 9174 | User | Workflow | Restore archived account |
| GET | /api/workflow/accounts/archived | 9217 | User | Workflow | List archived accounts |
| POST | /api/workflow/accounts/update | 9245 | User | Workflow | Update account metadata |
| POST | /api/workflow/accounts/bulk-update | 9303 | User | Workflow | Bulk update accounts |
| GET | /vendor-corrections | 9351 | User | Vendor Corrections | Vendor corrections |
| GET | /api/vendor-corrections/suspects | 9371 | User | Vendor Corrections | Get suspect vendors |
| POST | /api/vendor-corrections/analyze | 9592 | User | Vendor Corrections | Analyze vendor data |
| POST | /api/vendor-corrections/apply | 9615 | User | Vendor Corrections | Apply corrections |
| GET | /api/vendor-corrections/history | 9702 | User | Vendor Corrections | Correction history |
| GET | /account-gap-analysis | 9726 | User | Account Gap | Gap analysis dashboard |
| POST | /api/account-gap-analysis/upload | 9737 | User | Account Gap | Upload gap analysis |
| POST | /api/account-gap-analysis/run | 9851 | User | Account Gap | Run gap analysis |
| POST | /api/account-gap-analysis/add-missing | 10060 | User | Account Gap | Add missing accounts |
| POST | /api/directed/generate | 11551 | User | Directed | Generate directed plan |
| DELETE | /api/directed/plan | 11569 | User | Directed | Delete directed plan |
| GET | /api/directed/plan | 11586 | User | Directed | Get directed plan |
| POST | /api/directed/complete | 11598 | User | Directed | Mark task complete |
| POST | /api/directed/incomplete | 11679 | User | Directed | Mark task incomplete |
| POST | /api/directed/end-of-day | 11729 | User | Directed | End of day summary |
| POST | /api/directed/batch-history | 11780 | User | Directed | Batch history update |
| POST | /api/directed/bulk-complete | 11867 | User | Directed | Bulk mark complete |
| GET | /api/directed/rate | 11938 | User | Directed | Get task rate |
| GET | /api/directed/history | 11948 | User | Directed | Get task history |
| GET | /api/directed/reason-codes | 11987 | User | Directed | Get reason codes |
| POST | /api/directed/relink-scraper | 11993 | User | Directed | Relink scraper account |
| GET | /api/directed/scraper-links | 12005 | User | Directed | Get scraper links |
| GET | /api/directed/bw-lookup | 12015 | User | Directed | Bandwidth lookup |
| GET | /api/directed/team-summary | 12025 | User | Directed | Team summary |
| GET | /track | 12055 | User | Tracker | Pipeline tracker |
| GET | /api/track | 24038 | User | Tracker | Get tracked bills |
| GET | /debug | 12060 | User | Debug | Debug dashboard |
| GET | /failed | 12068 | User | Failure Analysis | Failed jobs view |
| GET | /metrics | 12076 | User | Metrics | Metrics dashboard |
| GET | /api/metrics/user-timing | 12082 | User | Metrics | User timing analytics |
| GET | /api/metrics/parsing-volume | 12148 | User | Metrics | Parsing volume stats |
| GET | /api/metrics/pipeline-summary | 12185 | User | Metrics | Pipeline summary |
| GET | /api/parser/throughput | 12343 | User | Metrics | Parser throughput |
| GET | /api/parser/queue-depth | 12424 | User | Metrics | Queue depth |
| GET | /api/bill/{pdf_id}/events | 12481 | User | Metrics | Bill event timeline |
| GET | /pipeline | 12518 | User | Pipeline | Pipeline queue view |
| GET | /bill/timeline | 12524 | User | Pipeline | Bill timeline |
| GET | /my-bills | 12530 | User | Pipeline | User's assigned bills |
| GET | /api/my-bills | 12536 | User | Pipeline | Get my bills data |
| GET | /transactions | 12604 | User | Pipeline | Transactions view |
| GET | /api/transactions/summary | 12610 | User | Pipeline | Transactions summary |
| GET | /api/pipeline/queue | 12722 | User | Pipeline | Get queue data |
| GET | /api/pipeline/bill/{pdf_id} | 12789 | User | Pipeline | Get bill detail |
| GET | /api/pipeline/stuck | 12831 | User | Pipeline | Find stuck bills |
| GET | /api/pipeline/stats | 12892 | User | Pipeline | Pipeline stats |
| GET | /autonomy-sim | 12931 | User | Autonomy | Autonomy simulator |
| GET | /api/autonomy/sim | 12939 | User | Autonomy | Get autonomy sim data |
| POST | /api/autonomy/sim/run | 12958 | User | Autonomy | Run autonomy simulation |
| GET | /api/autonomy/sim/bill/{pdf_id} | 12976 | User | Autonomy | Get autonomy sim bill |
| GET | /api/metrics/submitter-stats | 13046 | User | Metrics | Submitter statistics |
| GET | /api/metrics/week-over-week | 13450 | User | Metrics | Week-over-week stats |
| GET | /api/metrics/late-fees | 13751 | User | Metrics | Late fees analysis |
| GET | /api/metrics/activity-detail | 13929 | User | Metrics | Activity detail |
| GET | /api/metrics/overrides | 14191 | User | Metrics | Override analytics |
| GET | /api/metrics/outliers | 16113 | User | Metrics | Outlier records |
| POST | /api/metrics/outliers/{pdf_id}/review | 16158 | User | Metrics | Review outlier |
| GET | /api/metrics/account-stats/{account_key:path} | 16191 | User | Metrics | Account statistics |
| POST | /api/metrics/outliers/scan | 16216 | User | Metrics | Scan for outliers |
| GET | /api/metrics/logins | 16373 | User | Metrics | Login analytics |
| GET | /api/metrics/job-log | 16507 | User | Metrics | Job log |
| GET | /perf | 16615 | User | Perf Monitor | Performance monitoring |
| GET | /api/perf/live | 16631 | User | Perf Monitor | Live performance data |
| GET | /api/perf/rollups | 16669 | User | Perf Monitor | Historical rollups |
| GET | /api/perf/slow | 16685 | User | Perf Monitor | Slow queries |
| GET | /admin | 16623 | User | Admin | Admin dashboard |
| POST | /api/admin/backfill-posted-metadata | 16697 | Admin | Admin | Backfill posted metadata |
| POST | /api/admin/backfill-late-fees | 16753 | Admin | Admin | Backfill late fees |
| GET | /api/failed/jobs | 16881 | User | Failure Analysis | Failed jobs list |
| GET | /api/failed/errors | 16981 | User | Failure Analysis | Failed errors |
| POST | /api/failed/retry | 17079 | User | Failure Analysis | Retry failed job |
| POST | /api/failed/delete | 17101 | User | Failure Analysis | Delete failed job |
| GET | /api/catalog/vendors | 18611 | User | Catalog | Vendor catalog |
| GET | /api/catalog/properties | 18664 | User | Catalog | Property catalog |
| GET | /api/catalog/gl-accounts | 18699 | User | Catalog | GL account catalog |
| GET | /api/config/accounts-to-track | 18745 | User | Config | Get accounts to track |
| POST | /api/config/accounts-to-track | 18773 | User | Config | Save accounts to track |
| POST | /api/config/account-comment | 18808 | User | Config | Add account comment |
| POST | /api/config/account-skip-reason | 18873 | User | Config | Set account skip reason |
| POST | /api/config/toggle-ubi-tracking | 18942 | User | Config | Toggle UBI tracking |
| POST | /api/config/add-to-tracker | 19018 | User | Config | Add to tracker |
| POST | /api/config/add-to-ubi | 19111 | User | Config | Add to UBI |
| POST | /api/ubi/add-to-tracker | 19199 | User | Config | Add to tracker (UBI) |
| POST | /api/ubi/add-to-ubi | 19311 | User | Config | Add to UBI |
| POST | /api/ubi/remove-from-tracker | 19423 | User | Config | Remove from tracker |
| POST | /api/ubi/remove-from-ubi | 19470 | User | Config | Remove from UBI |
| GET | /api/config/gl-charge-code-mapping | 19682 | User | Config | Get GL charge code mapping |
| POST | /api/config/gl-charge-code-mapping | 19693 | User | Config | Save GL charge code mapping |
| GET | /api/config/vendor-property-overrides | 22846 | User | Config | Get vendor-property overrides |
| POST | /api/config/vendor-property-overrides | 22851 | User | Config | Save overrides |
| GET | /api/config/vendor-gl-overrides | 22892 | User | Config | Get vendor-GL overrides |
| POST | /api/config/vendor-gl-overrides | 22897 | User | Config | Save overrides |
| GET | /api/config/ap-team | 22601 | User | Config | Get AP team config |
| POST | /api/config/ap-team | 22619 | User | Config | Save AP team config |
| GET | /api/config/ubi-mapping | 22642 | User | Config | Get UBI mapping |
| POST | /api/config/ubi-mapping | 22687 | User | Config | Save UBI mapping |
| GET | /api/config/uom-mapping | 22721 | User | Config | Get UOM mapping |
| POST | /api/config/uom-mapping | 22749 | User | Config | Save UOM mapping |
| GET | /api/config/ap-mapping | 22803 | User | Config | Get AP mapping |
| POST | /api/config/ap-mapping | 22821 | User | Config | Save AP mapping |
| GET | /api/debug/exclusion-hashes | 22940 | User | Debug | Debug exclusion hashes |
| GET | /api/debug/orphaned-stage7 | 22994 | User | Debug | Find orphaned Stage 7 |
| POST | /api/debug/cleanup-orphaned-stage7 | 23088 | User | Debug | Clean up orphaned Stage 7 |
| GET | /api/debug/reports | 23149 | User | Debug | List debug reports |
| GET | /api/debug/stats | 23203 | User | Debug | Debug stats |
| GET | /api/debug/weekly-report | 23269 | User | Debug | Weekly report |
| GET | /api/debug/release-notes | 23374 | User | Debug | Release notes |
| POST | /api/debug/report | 23448 | User | Debug | Create debug report |
| POST | /api/debug/report/{report_id}/update | 23535 | User | Debug | Update debug report |
| DELETE | /api/debug/report/{report_id} | 23596 | User | Debug | Delete debug report |
| POST | /api/debug/upload-screenshot | 23937 | User | Debug | Upload screenshot |
| GET | /api/debug/report/{report_id}/screenshots | 23999 | User | Debug | Get report screenshots |
| GET | /api/audit/test-digest | 23919 | User | Audit | Test audit digest |
| POST | /api/delete_preentrata | 24296 | User | Core | Delete Pre-Entrata file |
| GET | /day | 24347 | User | Review | Daily invoice review |
| GET | /invoices | 24365 | User | Review | Invoice list |
| GET | /api/dates | 26444 | User | Review | Get available dates |
| GET | /api/day | 26449 | User | Review | Get day data |
| GET | /api/invoices | 26464 | User | Review | Get invoices |
| GET | /api/invoices_status | 26511 | User | Review | Get invoice statuses |
| GET | /api/catalogs | 26261 | User | Review | Get catalogs |
| GET | /api/options | 26388 | User | Review | Get select options |
| GET | /api/drafts | 26633 | User | Review | Get draft invoices |
| GET | /api/drafts/new-lines | 26651 | User | Review | Get new line drafts |
| POST | /api/drafts/batch | 26704 | User | Review | Batch get drafts |
| PUT | /api/drafts | 26791 | User | Review | Save draft invoice |
| GET | /api/timing/{invoice_id} | 26863 | User | Review | Get timing data |
| POST | /api/timing/{invoice_id}/start | 26876 | User | Review | Start timing |
| POST | /api/timing/{invoice_id}/heartbeat | 26887 | User | Review | Heartbeat |
| POST | /api/timing/{invoice_id}/stop | 26930 | User | Review | Stop timing |
| GET | /api/timing/summary | 26966 | User | Review | Timing summary |
| POST | /api/overrides | 27018 | User | Review | Save overrides |
| POST | /api/status | 27032 | User | Review | Update status |
| POST | /api/submit | 27038 | User | Review | Submit for publication |
| POST | /api/validate-submit | 33087 | User | Review | Validate before submit |
| GET | /pdf | 27794 | User | Review | Get PDF |
| GET | /review | 25716 | User | Review | Invoice review page |
| POST | /api/bulk_assign_property | 24780 | User | Review | Bulk assign property |
| POST | /api/bulk_assign_vendor | 24883 | User | Review | Bulk assign vendor |
| POST | /api/bulk_rework | 24989 | User | Review | Bulk rework |
| POST | /api/split_bill | 25192 | User | Review | Split bill |
| POST | /api/rework | 25290 | User | Review | Rework bill |
| GET | /api/meters/scan | 28477 | User | Meters | Scan meters |
| GET | /api/meters/analytics | 28716 | User | Meters | Meter analytics |
| POST | /api/meters/rebuild | 28726 | User | Meters | Rebuild meter cache |
| GET | /api/meters/{meter_id}/readings | 28742 | User | Meters | Get meter readings |
| POST | /api/meters/reading/update | 28779 | User | Meters | Update reading |
| GET | /api/meters/duplicates | 28821 | User | Meters | Find duplicate meters |
| POST | /api/meters/merge | 28871 | User | Meters | Merge meters |
| POST | /api/meters/bulk-dismiss | 28917 | User | Meters | Bulk dismiss meters |
| POST | /api/meters/{meter_id}/update | 28953 | User | Meters | Update meter |
| POST | /api/meters/ai-clean | 28988 | User | Meters | AI clean meters |
| GET | /api/meters/ai-suggestions | 29113 | User | Meters | AI meter suggestions |
| POST | /api/meters/bulk-rescan | 29126 | User | Meters | Bulk rescan meters |
| GET | /chart-by-meter | 29147 | User | Meters | Chart by meter |
| GET | /api/billback/report/data | 29157 | User | Billback | Report data |
| GET | /api/billback/report/pdf | 29555 | User | Billback | Report PDF |
| GET | /api/billback/report/periods | 30007 | User | Billback | Report periods |
| GET | /portfolio-config | 30094 | User | Portfolio | Portfolio config |
| GET | /api/portfolio | 30100 | User | Portfolio | Get portfolio |
| GET | /api/portfolio/{property_code} | 30131 | User | Portfolio | Get property |
| POST | /api/portfolio/upload | 30145 | User | Portfolio | Upload portfolio |
| DELETE | /api/portfolio/{property_code} | 30258 | User | Portfolio | Delete property |
| POST | /api/portfolio/clear | 30285 | User | Portfolio | Clear portfolio |
| GET | /print-checks | 30439 | User | Print Checks | Print checks page |
| GET | /review-checks | 30447 | User | Print Checks | Review checks page |
| GET | /api/print-checks/posted-invoices | 30455 | User | Print Checks | Get posted invoices |
| POST | /api/print-checks/create-slip | 30700 | User | Print Checks | Create check slip |
| GET | /api/print-checks/my-slips | 30773 | User | Print Checks | Get user's slips |
| GET | /api/print-checks/slip/{check_slip_id} | 30782 | User | Print Checks | Get slip detail |
| DELETE | /api/print-checks/slip/{check_slip_id} | 30791 | User | Print Checks | Delete slip |
| GET | /api/print-checks/slip/{check_slip_id}/pdf | 30853 | User | Print Checks | Get slip PDF |
| GET | /api/print-checks/bulk-pdf | 31107 | User | Print Checks | Bulk PDF export |
| GET | /api/print-checks/slip/{check_slip_id}/pdf-status | 31322 | User | Print Checks | PDF generation status |
| GET | /api/review-checks/pending | 31347 | User | Print Checks | Get pending checks |
| GET | /api/review-checks/slip/{check_slip_id} | 31372 | User | Print Checks | Get check slip |
| GET | /api/review-checks/slip/{check_slip_id}/invoice/{invoice_index}/pdf | 31381 | User | Print Checks | Get invoice PDF |
| POST | /api/review-checks/approve/{check_slip_id} | 31462 | User | Print Checks | Approve check slip |
| POST | /api/review-checks/reject/{check_slip_id} | 31500 | User | Print Checks | Reject check slip |
| GET | /knowledge-base | 31541 | User | Knowledge Base | Knowledge base page |
| GET | /api/knowledge | 31547 | User | Knowledge Base | Get knowledge entries |
| POST | /api/knowledge | 31639 | User | Knowledge Base | Create knowledge entry |
| PUT | /api/knowledge/{entity_type}/{entity_id} | 31697 | User | Knowledge Base | Update knowledge entry |
| POST | /api/knowledge/{entity_type}/{entity_id}/verify | 31755 | User | Knowledge Base | Verify knowledge |
| DELETE | /api/knowledge/{entity_type}/{entity_id} | 31808 | User | Knowledge Base | Delete knowledge entry |
| GET | /api/knowledge/for-invoice | 31847 | User | Knowledge Base | Get knowledge for invoice |
| POST | /api/ai-review/analyze | 32170 | User | AI Review | Analyze with AI |
| GET | /api/ai-review/suggestion/{pdf_id} | 32406 | User | AI Review | Get AI suggestion |
| GET | /api/ai-review/stats | 33182 | User | AI Review | AI review stats |
| GET | /api/ai-learning/stats | 33265 | User | AI Review | Learning stats |
| GET | /api/ai-learning/quarantined | 33350 | User | AI Review | Quarantined patterns |
| POST | /api/ai-learning/review-pattern | 33386 | User | AI Review | Review pattern |
| POST | /api/ai-learning/flag-bad-data | 33424 | User | AI Review | Flag bad data |
| GET | /api/autonomy/config | 33619 | User | Autonomy | Get autonomy config |
| GET | /api/autonomy/config/{vendor_id} | 33639 | User | Autonomy | Get vendor autonomy config |
| POST | /api/autonomy/promote | 33652 | User | Autonomy | Promote vendor |
| POST | /api/autonomy/demote | 33729 | User | Autonomy | Demote vendor |
| POST | /api/autonomy/health-check | 33819 | User | Autonomy | Health check |
| GET | /ai-review-dashboard | 33829 | User | AI Review | AI review dashboard |
| GET | /submeter-rates | 33894 | User | Submeter | Submeter rates |
| POST | /api/submeter-rates/generate | 34179 | User | Submeter | Generate rates |
| GET | /api/submeter-rates/status | 34211 | User | Submeter | Rates status |
| GET | /api/submeter-rates/config | 34227 | User | Submeter | Rates config |
| POST | /api/submeter-rates/config | 34234 | User | Submeter | Save rates config |
| GET | /api/submeter-rates/export-csv | 34247 | User | Submeter | Export CSV |




---

## Endpoint counts by module (approximate)

| Module | Count |
|---|---|
| Review (core invoice editor) | ~24 |
| Billback | ~15 |
| UBI | ~14 |
| Config & Catalog | ~28 |
| Master Bills & Accrual | ~19 |
| Metrics & Analytics | ~22 |
| Workflow & Directed | ~18 |
| Debug / Admin / Failure | ~17 |
| Meters | ~13 |
| Print Checks / Review Checks | ~13 |
| Pipeline / Transactions | ~8 |
| Knowledge Base / AI Review | ~12 |
| Autonomy | ~5 |
| Portfolio | ~6 |
| Parse & Input | ~9 |
| Auth & Users | ~9 |
| Scraper | ~8 |
| Post & Entrata | ~9 |
| Flagged | ~7 |
| Account Manager / Gap Analysis | ~12 |
| Submeter rates | ~5 |
| Perf Monitor | ~4 |
| Core (home, health, etc.) | ~3 |
| **Total** | **~349** |

Notes:
- Counts are approximate; some endpoints straddle modules (e.g., `/api/billback/ubi/*` is assigned to UBI but touches both billback and UBI concerns).
- Some endpoints appear as aliases (e.g., `/api/ubi/add-to-tracker` vs `/api/config/add-to-tracker`). Flag as DRIFT candidates — verify during module review.

---

Next: [03_module_taxonomy.md](./03_module_taxonomy.md)
