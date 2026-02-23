# Local dev runner for Bill Review FastAPI app
# Usage: Right-click -> Run with PowerShell (or run from an elevated PowerShell)

$ErrorActionPreference = "Stop"

# -------- Config (override as needed) --------
$env:AWS_REGION = "us-east-1"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:BUCKET = "jrk-analytics-billing"
$env:ENRICH_PREFIX = "Bill_Parser_4_Enriched_Outputs/"
$env:OVERRIDE_PREFIX = "Bill_Parser_5_Overrides/"
$env:REVIEW_TABLE = "jrk-bill-review"
$env:REVIEW_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/789814232318/jrk-bill-process"
$env:APP_SECRET = "dev-secret-change-me"
$env:SECURE_COOKIES = "0"   # critical for http://localhost

# -------- Python venv --------
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path .venv)) {
  python -m venv .venv
}
. .\.venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r requirements.txt

Write-Host "Starting FastAPI on http://127.0.0.1:8080 ..."
uvicorn main:app --host 127.0.0.1 --port 8080 --reload
