# Bill Review App

Flask-based application and supporting infrastructure for reviewing parsed legal bills.

## Contents

- `app.py`, `main.py` — application entry points.
- `templates/` — Jinja2 HTML templates.
- `requirements.txt` — Python dependencies.
- `Dockerfile`, `buildspec.yml` — container build and CI/CD.
- `infra/` — infrastructure code and scripts (see `infra/README.md`).
- `data/` — local data artifacts used for testing (e.g., `parsed_bills.csv`).

## Local Run

```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Visit `http://localhost:5000`.

## Deployment

- Containerized via `Dockerfile`.
- Infra (Lambdas, policies, url shortener) documented under `infra/`.

## AWS Profile

- Defaults to `jrk-analytics-admin` in scripts; override as needed.
