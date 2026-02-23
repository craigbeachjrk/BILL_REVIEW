# URL Shortener (Bill Review Infra)

Minimal API for shortening URLs, used within the Bill Review ecosystem.

## Files

- `urlshort_index.py` — Lambda/API handler with two endpoints:
  - `POST /shorten` — body `{ "url": "https://...", "ttl_seconds": 86400 }`
  - `GET /{code}` — redirects to original URL (302)
- `urlshort-ddb-policy.json` — DynamoDB access policy example for the table.
- `urlshort.zip` — Packaged artifact (if present).

## Environment

- `TABLE_NAME` (default `jrk-url-short`)
- `BASE_DOMAIN` (optional — if not provided, domain inferred from request context)

## DynamoDB Schema

- Partition key: `code` (string)
- Attributes: `url` (string), `expireAt` (number; epoch seconds)
- TTL: enable on `expireAt` for auto-expiry.

## Deploy

Example using AWS SAM/Console or direct Lambda creation; minimal steps via console/CLI:

1. Create a DynamoDB table `jrk-url-short` with `code` as partition key and TTL on `expireAt`.
2. Create a Lambda from `urlshort_index.py` (Python 3.12), set env vars.
3. Front with API Gateway HTTP API:
   - `ANY /{proxy+}` to the Lambda, or map `POST /shorten` and `GET /{code}`.
4. Grant Lambda permission to read/write the table (use `urlshort-ddb-policy.json` as a template).

## Usage

- Shorten
```bash
curl -X POST https://<api-domain>/shorten -H 'Content-Type: application/json' -d '{"url":"https://example.com","ttl_seconds":604800}'
```
- Redirect
```bash
curl -I https://<api-domain>/<code>
```
