# Rework Lambda

Serverless function that forwards rework PDFs uploaded to S3 to the parser intake, preserving optional notes as S3 object metadata and a sidecar `.notes.json`.

## Files

- `rework_handler.py` — Lambda handler.
- `setup_rework.ps1` — Creates/updates role, function, permissions, and S3 notifications end-to-end.
- `update_rework_lambda_code.ps1` — Updates code and environment for an existing function.

## Defaults (override via parameters)

- Profile: `jrk-analytics-admin`
- Region: `us-east-1`
- Bucket: `jrk-analytics-billing`
- Lambda name: `jrk-bill-parser-rework`
- Role name: `jrk-bill-parser-rework-role`
- Prefixes:
  - `Bill_Parser_Rework_Input/` (source)
  - `Bill_Parser_1_Pending_Parsing/` (destination)
  - Others set by `setup_rework.ps1` for the broader pipeline

## Deploy (fresh or update)

1. First-time / full setup
   ```powershell
   ./setup_rework.ps1 -Profile jrk-analytics-admin -Region us-east-1 -Bucket jrk-analytics-billing
   ```
   - Creates IAM role with basic exec policy
   - Packages a minimal handler and creates/updates the Lambda
   - Grants S3 invoke and configures bucket notifications for `ReworkPrefix` with `.pdf` suffix
   - Sets env vars (bucket and prefixes)

2. Update code + env only
   ```powershell
   ./update_rework_lambda_code.ps1 -Profile jrk-analytics-admin -Region us-east-1 -Bucket jrk-analytics-billing
   ```

## Handler behavior

- Listens to `ObjectCreated` on `Bill_Parser_Rework_Input/` with `.pdf` suffix
- Reads optional sidecar JSON next to the uploaded PDF: `{original}.rework.json`
  - If `{"notes": "..."}` present, note is forwarded as metadata and adjacent `{dest}.notes.json`
- Copies to `Bill_Parser_1_Pending_Parsing/` with timestamped filename `YYYYMMDDThhmmssZ_REWORK_{base}`

## Testing

- Upload a sample PDF to `s3://jrk-analytics-billing/Bill_Parser_Rework_Input/` (optionally add `{same-name}.rework.json`)
- Check destination `Bill_Parser_1_Pending_Parsing/` for copied file and `{file}.notes.json`.
