"""
ECS Fargate agent runner for IMPROVE reports.

Reads a report from DynamoDB, clones the repo, runs Claude Code to implement
a fix/enhancement, opens a PR, and emails the results.

Environment variables:
    REPORT_ID           - DynamoDB report_id (required)
    DRY_RUN             - Set to "1" to skip Claude/git/PR steps (for testing)
    AWS_REGION          - AWS region (default: us-east-1)
    DEBUG_TABLE         - DynamoDB table name (default: jrk-bill-review-debug)
    S3_BUCKET           - S3 bucket for screenshots (default: jrk-analytics-billing)
    ANTHROPIC_SECRET    - Secrets Manager name for Anthropic key
    GH_SECRET           - Secrets Manager name for GitHub token
    REPO_SLUG           - GitHub repo slug (default: craigbeachjrk/BILL_REVIEW)
"""

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone

import boto3

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPORT_ID = os.environ.get("REPORT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DEBUG_TABLE = os.environ.get("DEBUG_TABLE", "jrk-bill-review-debug")
S3_BUCKET = os.environ.get("S3_BUCKET", "jrk-analytics-billing")
ANTHROPIC_SECRET = os.environ.get("ANTHROPIC_SECRET", "improve-agent/anthropic-api-key")
GH_SECRET = os.environ.get("GH_SECRET", "improve-agent/gh-token")
REPO_SLUG = os.environ.get("REPO_SLUG", "craigbeachjrk/BILL_REVIEW")
REPO_URL_TEMPLATE = "https://{}@github.com/{}.git"
CLONE_DIR = "/tmp/repo"

EMAIL_SENDER = os.environ.get("IMPROVE_EMAIL_SENDER", "noreply@jrkanalytics.com")
EMAIL_RECIPIENTS = ["cbeach@jrk.com"]

# Boto3 clients
ddb = boto3.client("dynamodb", region_name=AWS_REGION)
ses = boto3.client("ses", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)
secrets = boto3.client("secretsmanager", region_name=AWS_REGION)


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------
def read_report(report_id: str) -> dict:
    """Read IMPROVE report from DynamoDB, return plain dict."""
    resp = ddb.get_item(TableName=DEBUG_TABLE, Key={"report_id": {"S": report_id}})
    raw = resp.get("Item")
    if not raw:
        raise ValueError(f"Report {report_id} not found in {DEBUG_TABLE}")
    out = {}
    for k, v in raw.items():
        if "S" in v:
            out[k] = v["S"]
        elif "N" in v:
            out[k] = v["N"]
        elif "BOOL" in v:
            out[k] = v["BOOL"]
    return out


def update_status(report_id: str, status: str, extra: dict | None = None):
    """Update report status (and optional extra fields) in DynamoDB."""
    now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    expr = "SET #s = :s, updated_utc = :u"
    vals = {":s": {"S": status}, ":u": {"S": now_utc}}
    names = {"#s": "status"}
    if extra:
        for k, v in extra.items():
            expr += f", {k} = :{k}"
            vals[f":{k}"] = {"S": str(v)}
    ddb.update_item(
        TableName=DEBUG_TABLE,
        Key={"report_id": {"S": report_id}},
        UpdateExpression=expr,
        ExpressionAttributeValues=vals,
        ExpressionAttributeNames=names,
    )


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
def get_secret(name: str) -> str:
    resp = secrets.get_secret_value(SecretId=name)
    return resp["SecretString"].strip()


# ---------------------------------------------------------------------------
# Screenshot presigned URLs
# ---------------------------------------------------------------------------
def presign_screenshots(screenshot_keys_json: str) -> list[str]:
    """Generate presigned S3 URLs for screenshot keys (JSON-encoded list)."""
    try:
        keys = json.loads(screenshot_keys_json)
    except (json.JSONDecodeError, TypeError):
        return []
    urls = []
    for key in keys:
        if not key:
            continue
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=3600,
        )
        urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------
def build_prompt(report: dict, screenshot_urls: list[str]) -> str:
    """Build a type-specific Claude Code prompt from the report data."""
    rtype = report.get("type", "bug")
    title = report.get("title", "")
    desc = report.get("description", "")
    page_url = report.get("page_url", "")
    page_context_raw = report.get("page_context", "")

    # Parse page context JSON
    page_context = {}
    if page_context_raw:
        try:
            page_context = json.loads(page_context_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    # Type-specific instruction header
    type_instructions = {
        "bug": (
            "You are fixing a bug reported by a user. "
            "Find the root cause and implement a targeted fix. "
            "Read CLAUDE.md first for project conventions."
        ),
        "enhancement": (
            "You are implementing an enhancement requested by a user. "
            "Follow existing patterns and keep changes minimal. "
            "Read CLAUDE.md first for project conventions."
        ),
        "feature": (
            "You are implementing a new feature requested by a user. "
            "Design a minimal implementation that follows existing architecture. "
            "Read CLAUDE.md first for project conventions."
        ),
    }
    instruction = type_instructions.get(rtype, type_instructions["bug"])

    parts = [instruction, ""]
    parts.append(f"**Title:** {title}")
    parts.append(f"**Description:** {desc}")
    if page_url:
        parts.append(f"**Page URL:** {page_url}")

    # Page context details
    if page_context:
        ctx_parts = []
        if page_context.get("pathname"):
            ctx_parts.append(f"  - Path: {page_context['pathname']}")
        if page_context.get("viewport"):
            ctx_parts.append(f"  - Viewport: {page_context['viewport']}")
        if page_context.get("pageErrors"):
            errs = page_context["pageErrors"]
            if isinstance(errs, list) and errs:
                ctx_parts.append(f"  - Console errors: {json.dumps(errs[:10])}")
        if page_context.get("activeFilters"):
            ctx_parts.append(f"  - Active filters: {json.dumps(page_context['activeFilters'])}")
        if page_context.get("tableRowCount") is not None:
            ctx_parts.append(f"  - Table rows visible: {page_context['tableRowCount']}")
        if ctx_parts:
            parts.append("\n**Page Context:**")
            parts.extend(ctx_parts)

    # Screenshot references
    if screenshot_urls:
        parts.append(f"\n**Screenshots:** {len(screenshot_urls)} screenshot(s) were attached to this report.")
        for i, url in enumerate(screenshot_urls, 1):
            parts.append(f"  Screenshot {i}: {url}")

    parts.append("\n**IMPORTANT:** Do NOT deploy or run deploy_app.ps1. Only commit your changes.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Git / Claude / PR helpers
# ---------------------------------------------------------------------------
def run_cmd(cmd: list[str], cwd: str | None = None, env: dict | None = None,
            check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command, log it, return result."""
    merged_env = {**os.environ, **(env or {})}
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, env=merged_env,
                            capture_output=True, text=True, timeout=1200)
    if result.stdout:
        # Truncate long output for logging
        out = result.stdout[:2000]
        log(f"  stdout: {out}")
    if result.stderr:
        err = result.stderr[:2000]
        log(f"  stderr: {err}")
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed (rc={result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr[:500]}"
        )
    return result


def clone_repo(gh_token: str) -> str:
    """Clone the repo into CLONE_DIR, return path."""
    url = REPO_URL_TEMPLATE.format(gh_token, REPO_SLUG)
    run_cmd(["git", "clone", "--depth=1", url, CLONE_DIR])
    # Configure git identity for commits
    run_cmd(["git", "config", "user.email", "improve-agent@jrkanalytics.com"], cwd=CLONE_DIR)
    run_cmd(["git", "config", "user.name", "IMPROVE Agent"], cwd=CLONE_DIR)
    return CLONE_DIR


def create_branch(report: dict) -> str:
    """Create and checkout a new branch."""
    rtype = report.get("type", "bug")
    short_id = report.get("report_id", "unknown")[:8]
    branch = f"improve/{rtype}-{short_id}"
    run_cmd(["git", "checkout", "-b", branch], cwd=CLONE_DIR)
    return branch


def run_claude(prompt: str, anthropic_key: str) -> dict:
    """Run Claude Code CLI and return parsed JSON output."""
    env = {"ANTHROPIC_API_KEY": anthropic_key}
    result = run_cmd(
        ["claude", "-p", prompt, "--dangerously-skip-permissions", "--output-format", "json"],
        cwd=CLONE_DIR,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude Code exited with rc={result.returncode}: {result.stderr[:500]}")
    # Parse JSON output
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"result": result.stdout[:5000]}


def commit_and_push(branch: str, report: dict) -> bool:
    """Stage, commit, and push. Returns True if there were changes."""
    # Check for changes
    status = run_cmd(["git", "status", "--porcelain"], cwd=CLONE_DIR, check=False)
    if not status.stdout.strip():
        log("No changes to commit.")
        return False

    run_cmd(["git", "add", "-A"], cwd=CLONE_DIR)

    title = report.get("title", "improvement")
    rtype = report.get("type", "bug")
    msg = (
        f"[IMPROVE] {rtype}: {title}\n\n"
        f"Report ID: {report.get('report_id', 'N/A')}\n"
        f"Requested by: {report.get('requestor', 'unknown')}\n\n"
        f"Co-Authored-By: Claude Code <noreply@anthropic.com>"
    )
    run_cmd(["git", "commit", "-m", msg], cwd=CLONE_DIR)
    run_cmd(["git", "push", "-u", "origin", branch], cwd=CLONE_DIR)
    return True


def create_pr(branch: str, report: dict, claude_output: dict) -> str:
    """Create a GitHub PR and return its URL."""
    title = report.get("title", "Improvement")
    rtype = report.get("type", "bug")
    requestor = report.get("requestor", "unknown")
    report_id = report.get("report_id", "")

    # Extract summary from Claude output
    summary = ""
    if isinstance(claude_output, dict):
        summary = claude_output.get("result", str(claude_output))[:2000]
    if not summary:
        summary = "See commits for details."

    pr_title = f"[IMPROVE] {rtype}: {title}"[:70]
    pr_body = (
        f"## Summary\n"
        f"- **Type:** {rtype}\n"
        f"- **Requested by:** {requestor}\n"
        f"- **Report ID:** `{report_id}`\n\n"
        f"## Agent Summary\n"
        f"{summary}\n\n"
        f"## Description\n"
        f"{report.get('description', '')}\n\n"
        f"---\n"
        f"*Automated PR created by IMPROVE Agent*"
    )

    result = run_cmd(
        ["gh", "pr", "create",
         "--title", pr_title,
         "--body", pr_body,
         "--base", "main",
         "--head", branch],
        cwd=CLONE_DIR,
    )
    pr_url = result.stdout.strip()
    return pr_url


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def send_result_email(report: dict, pr_url: str, summary: str, success: bool):
    """Send SES email with agent results."""
    requestor = report.get("requestor", "")
    title = report.get("title", "")
    rtype = report.get("type", "bug")

    to_list = list(EMAIL_RECIPIENTS)
    if requestor and "@" in requestor and requestor not in to_list:
        to_list.append(requestor)

    if success:
        subject = f"[IMPROVE Agent] PR Created: {title}"
        body = (
            f"<h2>IMPROVE Agent - PR Created</h2>"
            f"<p><b>Type:</b> {rtype}<br>"
            f"<b>Title:</b> {title}<br>"
            f"<b>Requested by:</b> {requestor}</p>"
            f"<p><b>Pull Request:</b> <a href='{pr_url}'>{pr_url}</a></p>"
            f"<h3>Agent Summary</h3>"
            f"<pre>{summary[:3000]}</pre>"
            f"<hr><p><i>Review the PR and merge if the changes look good.</i></p>"
        )
    else:
        subject = f"[IMPROVE Agent] Failed: {title}"
        body = (
            f"<h2>IMPROVE Agent - Failed</h2>"
            f"<p><b>Type:</b> {rtype}<br>"
            f"<b>Title:</b> {title}<br>"
            f"<b>Requested by:</b> {requestor}</p>"
            f"<h3>Error</h3>"
            f"<pre>{summary[:3000]}</pre>"
            f"<hr><p><i>The agent was unable to complete this task. Manual intervention required.</i></p>"
        )

    try:
        ses.send_email(
            Source=EMAIL_SENDER,
            Destination={"ToAddresses": to_list},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": body}},
            },
        )
        log(f"Email sent to {to_list}")
    except Exception as e:
        log(f"Email send failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not REPORT_ID:
        print("ERROR: REPORT_ID environment variable is required", file=sys.stderr)
        sys.exit(1)

    log(f"Starting IMPROVE agent for report: {REPORT_ID}")
    report = {}

    try:
        # 1. Read report
        report = read_report(REPORT_ID)
        log(f"Report loaded: type={report.get('type')}, title={report.get('title', '')[:60]}")

        # 2. Update status
        update_status(REPORT_ID, "Agent Running")

        if DRY_RUN:
            log("DRY_RUN mode — building prompt only, skipping Claude/git/PR")
            screenshot_urls = presign_screenshots(report.get("screenshots", ""))
            prompt = build_prompt(report, screenshot_urls)
            log(f"Prompt ({len(prompt)} chars):\n{prompt}")
            update_status(REPORT_ID, "Agent Dry Run", extra={"agent_prompt": prompt[:5000]})
            return

        # 3. Fetch secrets
        log("Fetching secrets...")
        anthropic_key = get_secret(ANTHROPIC_SECRET)
        gh_token = get_secret(GH_SECRET)
        os.environ["GH_TOKEN"] = gh_token  # gh CLI uses this

        # 4. Clone repo
        log("Cloning repository...")
        clone_repo(gh_token)

        # 5. Create branch
        branch = create_branch(report)
        log(f"Branch: {branch}")

        # 6. Build prompt
        screenshot_urls = presign_screenshots(report.get("screenshots", ""))
        prompt = build_prompt(report, screenshot_urls)
        log(f"Prompt built ({len(prompt)} chars)")

        # 7. Run Claude Code
        log("Running Claude Code...")
        claude_output = run_claude(prompt, anthropic_key)
        log("Claude Code finished")

        # Extract summary text
        agent_summary = ""
        if isinstance(claude_output, dict):
            agent_summary = claude_output.get("result", json.dumps(claude_output))[:5000]
        else:
            agent_summary = str(claude_output)[:5000]

        # 8. Commit and push
        has_changes = commit_and_push(branch, report)

        if not has_changes:
            update_status(REPORT_ID, "Agent No Changes", extra={
                "agent_summary": "Claude Code ran but produced no file changes.",
            })
            send_result_email(report, "", "No changes were made by the agent.", False)
            return

        # 9. Create PR
        log("Creating pull request...")
        pr_url = create_pr(branch, report, claude_output)
        log(f"PR created: {pr_url}")

        # 10. Update DynamoDB
        update_status(REPORT_ID, "Agent PR Created", extra={
            "pr_url": pr_url,
            "agent_summary": agent_summary,
        })

        # 11. Send success email
        send_result_email(report, pr_url, agent_summary, True)
        log("Done — success!")

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        log(f"FAILED: {error_msg}")
        try:
            update_status(REPORT_ID, "Agent Failed", extra={
                "agent_error": str(e)[:2000],
            })
        except Exception:
            pass
        send_result_email(report, "", error_msg, False)
        sys.exit(1)


if __name__ == "__main__":
    main()
