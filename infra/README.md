# Bill Review App Infrastructure

This directory contains infrastructure code and scripts for the Bill Review application.

## Structure

- `rework_lambda/`
  - Lambda function and helper scripts that forward rework PDFs from S3 into the parser intake.
- `policies/`
  - IAM trust and inline policies, plus helper scripts for attaching them.
- `urlshort/`
  - Minimal URL shortener function and policy used by Bill Review (e.g., sharing links).
- `../data/`
  - App data artifacts such as `parsed_bills.csv` used for testing/repro.

## AWS Profiles

Scripts are written to use the AWS profile `jrk-analytics-admin` by default (your AWS IAM Identity Center admin SSO user). Override via the `-Profile` parameter if needed.

## Quick Start

1. Rework Lambda
   - See `rework_lambda/README.md` for deployment and environment details.
2. Policies
   - See `policies/README.md` for the specific policies and how to apply them.
3. URL Shortener
   - See `urlshort/README.md` for packaging, deployment, and API usage.

## Notes

- S3 bucket used throughout: `jrk-analytics-billing`.
- Key prefixes used by the bill parsing pipeline (examples):
  - `Bill_Parser_Rework_Input/`
  - `Bill_Parser_1_Pending_Parsing/`
  - `Bill_Parser_2_Post_Parsed_Outputs/`
  - `Bill_Parser_4_Enriched_Outputs/`
  - `Bill_Parser_6_PreEntrata_Submission/`
