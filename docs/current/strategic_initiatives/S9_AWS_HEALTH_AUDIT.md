# S9: AWS Environment Health Audit

## Status: NOT STARTED
## Priority: HIGH — vendor cache was silently broken for a month with no alerting

## Problem

The vendor-cache-builder Lambda was OOM-failing on every run for ~30 days before anyone noticed.
There is no systematic health monitoring across the AWS environment. Other Lambdas, cron jobs,
or data pipelines could be silently failing right now.

## Audit Scope

### Lambda Functions — check ALL for errors
- [ ] `jrk-bill-router` — routing incoming bills
- [ ] `jrk-bill-parser` — parsing PDFs
- [ ] `jrk-bill-large-parser` — large file parsing
- [ ] `jrk-bill-chunk-processor` — chunk processing
- [ ] `jrk-bill-enricher` — vendor/property enrichment
- [ ] `jrk-bill-aggregator` — aggregation
- [ ] `jrk-bill-parser-failure-router` — failure routing
- [ ] `jrk-bill-parser-rework` — rework processing
- [ ] `jrk-email-ingest` — email bill ingestion
- [ ] `vendor-cache-builder` — vendor dimension refresh (WAS BROKEN)
- [ ] `jrk-vendor-validator` — vendor validation
- [ ] `jrk-vendor-notifier` — vendor notifications
- [ ] `jrk-meter-cleaner` — meter data cleanup
- [ ] `jrk-bw-lookup` — bandwidth lookups
- [ ] `jrk-presigned-upload` — presigned URL generation
- [ ] `jrk-data-feeds-executor` — Entrata data feeds
- [ ] `jrk-bill-index-builder` — bill index cache builder

### For each Lambda, check:
1. **Error rate** — any errors in last 7 days?
2. **Memory usage** — is Max Memory Used close to allocated? (OOM risk)
3. **Duration** — any timeouts or near-timeouts?
4. **Invocation count** — is it actually running on schedule?
5. **Dead letter queue** — any unprocessed failures?

### EventBridge Rules — verify all cron jobs are firing
- [ ] `vendor-cache-hourly` — hourly vendor cache refresh
- [ ] `jrk-data-feeds-entrata-vendors` — daily vendor feed (was it ever implemented?)
- [ ] `jrk-data-feeds-entrata-vendor-locations` — weekly vendor locations
- [ ] `jrk-data-feeds-entrata-vendor-picklists` — weekly vendor picklists
- [ ] Any other scheduled rules

### Data Pipeline Health
- [ ] S3 stage file counts — are files flowing through stages?
- [ ] DynamoDB table sizes — any unexpected growth?
- [ ] Pipeline tracker events — any gaps in event flow?

### Monitoring to Add
- [ ] CloudWatch alarms for ALL Lambda error rates
- [ ] CloudWatch alarm for Lambda OOM (memory > 90% of allocation)
- [ ] CloudWatch alarm for Lambda timeouts
- [ ] Slack/email notification channel for alarms
- [ ] Weekly health report dashboard

## Deliverable
A health check script that can be run on-demand or scheduled to scan the entire
AWS environment and report any issues. Similar to `tests/smoke_test_production.py`
but for AWS infrastructure.
