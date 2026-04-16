# IMPROVE Button — Complete Template

> **Purpose:** Copy-paste blueprint for adding a self-healing "IMPROVE" button to any
> FastAPI web application. Users click IMPROVE, describe a bug/enhancement/feature,
> attach screenshots, and an autonomous Claude Code agent clones the repo, implements
> the fix, and opens a PR — all within minutes.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                            │
│                                                                     │
│  [IMPROVE] → Modal (type + title + desc + screenshots)              │
│       │                                                             │
│       ├─ POST /api/debug/upload-screenshot   (base64 → S3)          │
│       └─ POST /api/debug/report              (submit report)        │
└────────────────────────┬────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────────┐
│  FASTAPI BACKEND (AppRunner / ECS / EC2 / Lambda — anything)        │
│                                                                     │
│  1. Write report to DynamoDB                                        │
│  2. Send notification email via SES (fire-and-forget)               │
│  3. Launch ECS Fargate task (fire-and-forget)                       │
│       └─ Passes REPORT_ID as env var                                │
└────────────────────────┬────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────────┐
│  ECS FARGATE TASK (improve-agent container)                         │
│                                                                     │
│  1. Read report from DynamoDB                                       │
│  2. Fetch secrets (Anthropic API key + GitHub PAT)                  │
│  3. Clone repo → create branch                                      │
│  4. Build prompt with context + screenshots                         │
│  5. Run `claude -p "..." --dangerously-skip-permissions`            │
│  6. If file changes → commit → push → create PR via `gh`           │
│  7. Update DynamoDB status + send result email                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Table of Contents

1. [Frontend: HTML + CSS + JavaScript](#1-frontend)
2. [Backend: FastAPI Endpoints](#2-backend-fastapi-endpoints)
3. [ECS Agent: Dockerfile](#3-ecs-agent-dockerfile)
4. [ECS Agent: Python Runner](#4-ecs-agent-python-runner)
5. [AWS Infrastructure Setup Script](#5-aws-infrastructure-setup)
6. [AWS Secrets Manager](#6-aws-secrets)
7. [Environment Variables](#7-environment-variables)
8. [IAM Permissions](#8-iam-permissions)
9. [Deployment Checklist](#9-deployment-checklist)

---

## 1. Frontend

Drop this into any HTML template. It's fully self-contained (no external JS deps).

### 1a. CSS (in `<head>`)

```css
/* IMPROVE modal styles */
.improve-modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:10000;align-items:center;justify-content:center}
.improve-modal-overlay.show{display:flex}
.improve-modal-box{background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.3);max-width:540px;width:92%;padding:24px;max-height:calc(100vh - 60px);overflow-y:auto}
.improve-modal-box h2{margin:0 0 12px 0;font-size:18px}
.improve-modal-box label{display:block;margin-bottom:4px;font-weight:600;font-size:13px}
.improve-modal-box input,.improve-modal-box textarea{width:100%;padding:10px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:12px;font-family:inherit;font-size:14px;box-sizing:border-box}
.improve-modal-box textarea{min-height:100px;resize:vertical}
.improve-modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
.improve-toast{position:fixed;top:20px;right:20px;background:#111827;color:#fff;padding:12px 16px;border-radius:10px;box-shadow:0 8px 20px rgba(0,0,0,.25);z-index:10001;opacity:0;transition:opacity .3s}
.improve-toast.show{opacity:.96}
.improve-toast.ok{background:#0f766e}
.improve-toast.err{background:#b91c1c}
.improve-path-badge{display:inline-block;background:#f1f5f9;color:#475569;font-size:12px;padding:3px 10px;border-radius:6px;margin-bottom:14px;font-family:monospace;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.improve-type-group{display:flex;gap:0;margin-bottom:14px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
.improve-type-group label{display:flex;align-items:center;justify-content:center;flex:1;padding:8px 4px;margin:0;font-size:13px;font-weight:500;cursor:pointer;background:#fff;border-right:1px solid #e5e7eb;transition:background .15s,color .15s;text-align:center}
.improve-type-group label:last-child{border-right:none}
.improve-type-group input[type=radio]{display:none}
.improve-type-group label.sel-bug{background:#fee2e2;color:#991b1b}
.improve-type-group label.sel-enhancement{background:#ede9fe;color:#5b21b6}
.improve-type-group label.sel-feature{background:#d1fae5;color:#065f46}
.improve-ss-area{border:2px dashed #d1d5db;border-radius:10px;padding:14px;text-align:center;color:#94a3b8;font-size:13px;margin-bottom:12px;cursor:pointer;transition:border-color .2s,background .2s;min-height:40px}
.improve-ss-area:hover,.improve-ss-area.drag{border-color:#0ea5e9;background:#f0f9ff}
.improve-ss-strip{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.improve-ss-thumb{position:relative;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;height:72px}
.improve-ss-thumb img{height:72px;display:block}
.improve-ss-thumb .ss-remove{position:absolute;top:2px;right:2px;background:rgba(0,0,0,.6);color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:13px;line-height:20px;text-align:center;cursor:pointer;padding:0}
.improve-ss-thumb .ss-remove:hover{background:#ef4444}
.improve-ss-thumb .improve-ss-uploading{opacity:.5}
```

### 1b. HTML (in `<body>`, anywhere)

```html
<!-- IMPROVE button — place in your header/nav -->
<button class="btn secondary" id="improveBtn">IMPROVE</button>

<!-- Toast notification -->
<div id="improveToast" class="improve-toast"></div>

<!-- Modal -->
<div id="improveModal" class="improve-modal-overlay">
  <div class="improve-modal-box">
    <h2>Report an Issue or Idea</h2>
    <div class="improve-path-badge" id="improvePagePath"></div>

    <div class="improve-type-group" id="improveTypeGroup">
      <label class="sel-bug" id="improveTypeBugLbl">
        <input type="radio" name="improveType" value="bug" checked /><span>Bug</span>
      </label>
      <label id="improveTypeEnhLbl">
        <input type="radio" name="improveType" value="enhancement" /><span>Enhancement</span>
      </label>
      <label id="improveTypeFeatLbl">
        <input type="radio" name="improveType" value="feature" /><span>Feature Request</span>
      </label>
    </div>

    <label for="reportTitle">Title</label>
    <input type="text" id="reportTitle" placeholder="Brief summary" />

    <label for="reportDesc">Description</label>
    <textarea id="reportDesc" placeholder="Describe what happened or what you'd like improved"></textarea>

    <label>Screenshots</label>
    <div class="improve-ss-strip" id="improveSsStrip"></div>
    <div class="improve-ss-area" id="improveSsArea">Paste (Ctrl+V) or click to add screenshots (max 5)</div>
    <input type="file" id="improveSsFile" accept="image/*" multiple style="display:none" />

    <div class="improve-modal-actions">
      <button class="btn secondary" id="cancelReport">Cancel</button>
      <button class="btn" id="submitReport">Submit</button>
    </div>
  </div>
</div>
```

### 1c. JavaScript (in `<script>`, end of body)

```javascript
// IMPROVE modal functionality — v2 with screenshots, type, page context
(function(){
  // ---------- helpers ----------
  function showImproveToast(msg, type){
    const t = document.getElementById('improveToast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'improve-toast ' + (type === 'ok' ? 'ok' : 'err');
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
  }

  // ---------- refs ----------
  const modal       = document.getElementById('improveModal');
  const improveBtn  = document.getElementById('improveBtn');
  const cancelBtn   = document.getElementById('cancelReport');
  const submitBtn   = document.getElementById('submitReport');
  const titleInput  = document.getElementById('reportTitle');
  const descInput   = document.getElementById('reportDesc');
  const ssArea      = document.getElementById('improveSsArea');
  const ssStrip     = document.getElementById('improveSsStrip');
  const ssFileInput = document.getElementById('improveSsFile');
  const pathBadge   = document.getElementById('improvePagePath');
  const typeGroup   = document.getElementById('improveTypeGroup');

  if (!improveBtn || !modal) return;

  // ---------- state ----------
  let _ssKeys = [];        // uploaded S3 keys
  let _ssUploading = 0;
  let _tempReportId = '';  // generated before first upload so screenshots share an id
  let _consoleErrors = []; // captured console errors

  // capture console errors for page context
  window.addEventListener('error', function(e){
    _consoleErrors.push({msg: e.message || '', src: e.filename || '', line: e.lineno || 0});
    if (_consoleErrors.length > 20) _consoleErrors.shift();
  });

  // ---------- type selector ----------
  function updateTypeStyles(){
    const checked = typeGroup.querySelector('input[type=radio]:checked');
    typeGroup.querySelectorAll('label').forEach(function(lbl){ lbl.className = ''; });
    if (checked) { checked.parentElement.className = 'sel-' + checked.value; }
  }
  typeGroup.querySelectorAll('input[type=radio]').forEach(function(r){
    r.addEventListener('change', updateTypeStyles);
  });

  // ---------- page path ----------
  function refreshPathBadge(){
    pathBadge.textContent = window.location.pathname + (window.location.search || '');
    pathBadge.title = window.location.href;
  }

  // ---------- open / close ----------
  function resetModal(){
    titleInput.value = '';
    descInput.value = '';
    ssStrip.innerHTML = '';
    _ssKeys = [];
    _ssUploading = 0;
    _tempReportId = '';
    const bugRadio = typeGroup.querySelector('input[value=bug]');
    if (bugRadio) { bugRadio.checked = true; updateTypeStyles(); }
  }

  improveBtn.addEventListener('click', function(){
    _tempReportId = crypto.randomUUID ? crypto.randomUUID()
      : ('xx-' + Date.now() + '-' + Math.random().toString(36).slice(2));
    refreshPathBadge();
    modal.classList.add('show');
    titleInput.focus();
  });

  cancelBtn.addEventListener('click', function(){ modal.classList.remove('show'); resetModal(); });
  modal.addEventListener('click', function(e){
    if (e.target === modal){ modal.classList.remove('show'); resetModal(); }
  });

  // ---------- screenshot helpers ----------
  function addScreenshotFile(file){
    if (!file || !file.type.startsWith('image/')) return;
    if (_ssKeys.length + _ssUploading >= 5){ showImproveToast('Max 5 screenshots', 'err'); return; }
    if (file.size > 5 * 1024 * 1024){ showImproveToast('Image exceeds 5 MB limit', 'err'); return; }

    const reader = new FileReader();
    reader.onload = function(){
      const dataUrl = reader.result;
      const contentType = file.type || 'image/png';

      // Create thumbnail immediately (with uploading opacity)
      const thumb = document.createElement('div');
      thumb.className = 'improve-ss-thumb improve-ss-uploading';
      const img = document.createElement('img');
      img.src = dataUrl;
      thumb.appendChild(img);
      const removeBtn = document.createElement('button');
      removeBtn.className = 'ss-remove';
      removeBtn.textContent = '\u00d7';
      thumb.appendChild(removeBtn);
      ssStrip.appendChild(thumb);

      _ssUploading++;

      // Upload to backend
      fetch('/api/debug/upload-screenshot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          report_id: _tempReportId,
          image_data: dataUrl,
          content_type: contentType
        })
      })
      .then(function(r){ return r.json(); })
      .then(function(data){
        _ssUploading--;
        if (data.ok){
          thumb.classList.remove('improve-ss-uploading');
          const key = data.key;
          _ssKeys.push(key);
          removeBtn.addEventListener('click', function(){
            thumb.remove();
            _ssKeys = _ssKeys.filter(function(k){ return k !== key; });
          });
        } else { thumb.remove(); showImproveToast(data.error || 'Upload failed', 'err'); }
      })
      .catch(function(){ _ssUploading--; thumb.remove(); showImproveToast('Screenshot upload failed', 'err'); });
    };
    reader.readAsDataURL(file);
  }

  // Paste (Ctrl+V) — only active when modal is open
  document.addEventListener('paste', function(e){
    if (!modal.classList.contains('show')) return;
    const items = (e.clipboardData || {}).items || [];
    for (let i = 0; i < items.length; i++){
      if (items[i].type.indexOf('image') !== -1){
        e.preventDefault(); addScreenshotFile(items[i].getAsFile()); return;
      }
    }
  });

  // Click-to-upload via hidden file input
  ssArea.addEventListener('click', function(){ ssFileInput.click(); });
  ssFileInput.addEventListener('change', function(){
    var files = ssFileInput.files || [];
    for (var i = 0; i < files.length; i++) addScreenshotFile(files[i]);
    ssFileInput.value = '';
  });

  // Drag-and-drop
  ssArea.addEventListener('dragover', function(e){ e.preventDefault(); ssArea.classList.add('drag'); });
  ssArea.addEventListener('dragleave', function(){ ssArea.classList.remove('drag'); });
  ssArea.addEventListener('drop', function(e){
    e.preventDefault(); ssArea.classList.remove('drag');
    var files = e.dataTransfer.files || [];
    for (var i = 0; i < files.length; i++) addScreenshotFile(files[i]);
  });

  // ---------- page context ----------
  function collectPageContext(){
    var ctx = {
      href: window.location.href,
      pathname: window.location.pathname,
      search: window.location.search,
      viewport: window.innerWidth + 'x' + window.innerHeight,
      userAgent: navigator.userAgent
    };
    var errEls = document.querySelectorAll('.error, .err, [role=alert]');
    var pageErrors = [];
    errEls.forEach(function(el){
      var txt = (el.textContent || '').trim().substring(0, 200);
      if (txt) pageErrors.push(txt);
    });
    if (pageErrors.length) ctx.pageErrors = pageErrors;
    if (_consoleErrors.length) ctx.consoleErrors = _consoleErrors.slice(-10);
    return ctx;
  }

  // ---------- submit ----------
  submitBtn.addEventListener('click', async function(){
    var title = titleInput.value.trim();
    var description = descInput.value.trim();
    if (!title || !description){ showImproveToast('Please fill in title and description', 'err'); return; }
    if (_ssUploading > 0){ showImproveToast('Screenshots still uploading...', 'err'); return; }

    var checkedType = typeGroup.querySelector('input[type=radio]:checked');
    var reportType = checkedType ? checkedType.value : 'bug';

    try {
      var resp = await fetch('/api/debug/report', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          title: title,
          description: description,
          page_url: window.location.href,
          type: reportType,
          screenshots: _ssKeys,
          page_context: collectPageContext()
        })
      });
      if (!resp.ok) throw new Error('Submit failed');
      showImproveToast('Report submitted!', 'ok');
      modal.classList.remove('show');
      resetModal();
    } catch (e) {
      showImproveToast('Error submitting report', 'err');
    }
  });
})();
```

### What the Frontend Sends

**Screenshot upload** → `POST /api/debug/upload-screenshot`
```json
{
  "report_id": "uuid-generated-client-side",
  "image_data": "data:image/png;base64,iVBOR...",
  "content_type": "image/png"
}
```

**Report submit** → `POST /api/debug/report`
```json
{
  "title": "Button doesn't save when clicked",
  "description": "On the invoices page, clicking Save does nothing...",
  "page_url": "https://myapp.com/invoices?date=2026-03-01",
  "type": "bug",
  "screenshots": ["improve-screenshots/uuid/file.png"],
  "page_context": {
    "href": "https://myapp.com/invoices?date=2026-03-01",
    "pathname": "/invoices",
    "search": "?date=2026-03-01",
    "viewport": "1920x1080",
    "userAgent": "Mozilla/5.0 ...",
    "pageErrors": ["TypeError: Cannot read property 'foo' of undefined"],
    "consoleErrors": [{"msg": "...", "src": "main.js", "line": 42}]
  }
}
```

---

## 2. Backend FastAPI Endpoints

### 2a. Environment Variables (add to your app config)

```python
import os

# IMPROVE Agent (ECS Fargate) — empty strings = disabled (safe default)
IMPROVE_AGENT_CLUSTER  = os.getenv("IMPROVE_AGENT_CLUSTER", "")
IMPROVE_AGENT_TASK_DEF = os.getenv("IMPROVE_AGENT_TASK_DEF", "")
IMPROVE_AGENT_SUBNETS  = [s for s in os.getenv("IMPROVE_AGENT_SUBNETS", "").split(",") if s]
IMPROVE_AGENT_SG       = os.getenv("IMPROVE_AGENT_SG", "")

# DynamoDB table for reports
DEBUG_TABLE = os.getenv("DEBUG_TABLE", "jrk-bill-review-debug")  # CHANGE THIS

# S3 bucket for screenshots
BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")  # CHANGE THIS

# SES email config
IMPROVE_EMAIL_SENDER     = os.getenv("IMPROVE_EMAIL_SENDER", "noreply@yourdomain.com")
IMPROVE_EMAIL_RECIPIENTS = ["team@yourdomain.com"]
```

### 2b. Boto3 Clients (add to your startup)

```python
import boto3
from concurrent.futures import ThreadPoolExecutor

_boto_config = botocore.config.Config(max_pool_connections=50, read_timeout=30)
s3   = boto3.client("s3",  region_name="us-east-1", config=_boto_config)
ddb  = boto3.client("dynamodb", region_name="us-east-1", config=_boto_config)
_ses = boto3.client("ses", region_name="us-east-1", config=_boto_config)
_ecs = boto3.client("ecs", region_name="us-east-1", config=_boto_config)

_GLOBAL_EXECUTOR = ThreadPoolExecutor(max_workers=20)
```

### 2c. Screenshot Upload Endpoint

```python
import base64
import uuid
import re as _re

_IMPROVE_SCREENSHOT_MAX_COUNT = 5
_IMPROVE_SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_IMPROVE_SCREENSHOT_PREFIX = "improve-screenshots"


@app.post("/api/debug/upload-screenshot")
async def api_upload_screenshot(request: Request, user: str = Depends(require_user)):
    """Upload a screenshot image (base64) to S3 for an IMPROVE report."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    report_id = str(payload.get("report_id") or "").strip()
    image_data = str(payload.get("image_data") or "").strip()
    content_type = str(payload.get("content_type") or "image/png").strip()

    if not report_id or not image_data:
        return JSONResponse({"error": "report_id and image_data required"}, status_code=400)

    # Validate report_id: no path separators or traversal
    if not _re.match(r'^[a-zA-Z0-9_-]{1,128}$', report_id):
        return JSONResponse({"error": "invalid report_id"}, status_code=400)

    # Validate content type
    ext_map = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
    if content_type not in ext_map:
        return JSONResponse({"error": f"unsupported image type: {content_type}"}, status_code=400)
    ext = ext_map[content_type]

    # Decode base64 (strip data URI prefix if present)
    try:
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]
        raw = base64.b64decode(image_data)
    except Exception:
        return JSONResponse({"error": "invalid base64 image data"}, status_code=400)

    if len(raw) > _IMPROVE_SCREENSHOT_MAX_BYTES:
        return JSONResponse({"error": "image exceeds 5MB limit"}, status_code=400)

    # Check existing screenshot count for this report
    try:
        existing = s3.list_objects_v2(
            Bucket=BUCKET,
            Prefix=f"{_IMPROVE_SCREENSHOT_PREFIX}/{report_id}/",
            MaxKeys=_IMPROVE_SCREENSHOT_MAX_COUNT + 1,
        )
        if existing.get("KeyCount", 0) >= _IMPROVE_SCREENSHOT_MAX_COUNT:
            return JSONResponse({"error": f"max {_IMPROVE_SCREENSHOT_MAX_COUNT} screenshots"}, status_code=400)
    except Exception:
        pass

    file_id = str(uuid.uuid4())
    s3_key = f"{_IMPROVE_SCREENSHOT_PREFIX}/{report_id}/{file_id}.{ext}"

    try:
        s3.put_object(Bucket=BUCKET, Key=s3_key, Body=raw, ContentType=content_type)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    presigned_url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": s3_key}, ExpiresIn=3600
    )
    return {"ok": True, "key": s3_key, "url": presigned_url}
```

### 2d. Report Submit Endpoint (the main one)

```python
import datetime as dt

@app.post("/api/debug/report")
async def api_create_debug_report(request: Request, user: str = Depends(require_user)):
    """Create a new IMPROVE report. Saves to DynamoDB, emails team, triggers ECS agent."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    title       = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    page_url    = str(payload.get("page_url") or "").strip()
    report_type = str(payload.get("type") or "bug").strip()
    priority    = str(payload.get("priority") or "Medium").strip()
    screenshots  = payload.get("screenshots") or []
    page_context = payload.get("page_context") or {}

    if not title or not description:
        return JSONResponse({"error": "title and description required"}, status_code=400)

    report_id = str(uuid.uuid4())
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    item = {
        "report_id":   {"S": report_id},
        "title":       {"S": title},
        "description": {"S": description},
        "page_url":    {"S": page_url},
        "requestor":   {"S": user},
        "status":      {"S": "Open"},
        "priority":    {"S": priority},
        "type":        {"S": report_type},
        "created_utc": {"S": now_utc},
        "updated_utc": {"S": now_utc},
    }
    if screenshots:
        item["screenshots"] = {"S": json.dumps(screenshots)}
    if page_context:
        item["page_context"] = {"S": json.dumps(page_context)}

    try:
        ddb.put_item(TableName=DEBUG_TABLE, Item=item)

        # Fire-and-forget: email notification
        try:
            subject = f"[{report_type.capitalize()}] {title} — from {user}"
            body_html = _build_improve_email_html(
                report_type, title, description, user, page_url,
                page_context, screenshots,
            )
            _GLOBAL_EXECUTOR.submit(_send_improve_email, subject, body_html,
                                     [user] if "@" in user else None)
        except Exception as email_err:
            print(f"[IMPROVE] Email failed (non-blocking): {email_err}")

        # Fire-and-forget: launch ECS Fargate agent
        if IMPROVE_AGENT_CLUSTER and IMPROVE_AGENT_TASK_DEF:
            try:
                _ecs.run_task(
                    cluster=IMPROVE_AGENT_CLUSTER,
                    taskDefinition=IMPROVE_AGENT_TASK_DEF,
                    launchType="FARGATE",
                    networkConfiguration={"awsvpcConfiguration": {
                        "subnets": IMPROVE_AGENT_SUBNETS,
                        "securityGroups": [IMPROVE_AGENT_SG],
                        "assignPublicIp": "ENABLED",
                    }},
                    overrides={"containerOverrides": [{
                        "name": "improve-agent",
                        "environment": [
                            {"name": "REPORT_ID", "value": report_id},
                        ],
                    }]},
                )
                print(f"[IMPROVE] ECS agent triggered for report {report_id}")
            except Exception as ecs_err:
                print(f"[IMPROVE] ECS trigger failed (non-blocking): {ecs_err}")

        return {"ok": True, "report_id": report_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
```

### 2e. Email Helper Functions

```python
def _send_improve_email(subject: str, body_html: str, extra_to: list[str] | None = None):
    """Fire-and-forget SES email."""
    to_list = list(IMPROVE_EMAIL_RECIPIENTS)
    if extra_to:
        for addr in extra_to:
            if addr and addr not in to_list:
                to_list.append(addr)
    try:
        _ses.send_email(
            Source=IMPROVE_EMAIL_SENDER,
            Destination={"ToAddresses": to_list},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Html": {"Data": body_html}},
            },
        )
    except Exception as e:
        print(f"[IMPROVE] Email send failed: {e}")


def _build_improve_email_html(report_type, title, description, requestor,
                               page_url, page_context, screenshot_keys):
    """Build HTML email body with type badge, context, and screenshot thumbnails."""
    import html as _html
    _esc = _html.escape

    type_colors = {"bug": "#ef4444", "enhancement": "#8b5cf6", "feature": "#10b981"}
    badge_color = type_colors.get(report_type, "#64748b")

    # Page context
    ctx_html = ""
    if page_context:
        ctx_parts = []
        if page_context.get("pathname"):
            ctx_parts.append(f"<b>Path:</b> {_esc(str(page_context['pathname']))}")
        if page_context.get("viewport"):
            ctx_parts.append(f"<b>Viewport:</b> {_esc(str(page_context['viewport']))}")
        if page_context.get("pageErrors"):
            ctx_parts.append(f"<b>Page errors:</b> {len(page_context['pageErrors'])} captured")
        if ctx_parts:
            ctx_html = "<p style='font-size:13px;color:#64748b'>" + " &bull; ".join(ctx_parts) + "</p>"

    # Screenshot thumbnails (presigned URLs)
    ss_html = ""
    if screenshot_keys:
        ss_links = []
        for key in screenshot_keys:
            try:
                url = s3.generate_presigned_url("get_object",
                    Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=604800)
                ss_links.append(
                    f'<a href="{_esc(url)}" target="_blank">'
                    f'<img src="{_esc(url)}" style="max-height:120px;border-radius:6px;'
                    f'border:1px solid #e5e7eb;margin-right:8px" /></a>'
                )
            except Exception:
                ss_links.append(f"<code>{_esc(str(key))}</code>")
        ss_html = ("<p><b>Screenshots:</b></p>"
                   "<div style='display:flex;flex-wrap:wrap;gap:8px'>"
                   + "".join(ss_links) + "</div>")

    return f"""
    <div style="font-family:Inter,system-ui,sans-serif;max-width:600px;margin:0 auto">
      <div style="padding:20px;background:#f8fafc;border-radius:12px;border:1px solid #e5e7eb">
        <span style="background:{badge_color};color:#fff;padding:4px 10px;border-radius:6px;
               font-size:12px;font-weight:600">{_esc(report_type.capitalize())}</span>
        <h2 style="margin:8px 0;font-size:18px">{_esc(title)}</h2>
        <p style="color:#334155;white-space:pre-wrap">{_esc(description)}</p>
        <p style="font-size:13px;color:#64748b">
          <b>From:</b> {_esc(requestor)} &bull; <b>Page:</b> {_esc(page_url)}
        </p>
        {ctx_html}
        {ss_html}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0" />
        <p style="font-size:12px;color:#94a3b8">
          <a href="https://YOURAPP.com/debug">View in Issue Tracker</a>
        </p>
      </div>
    </div>
    """
```

---

## 3. ECS Agent Dockerfile

**File:** `infra/improve-agent/Dockerfile`

```dockerfile
FROM python:3.12-slim

# System deps: git, curl, unzip for gh CLI, Node.js 22 for Claude Code
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl unzip ca-certificates gnupg && \
    # Node.js 22 (LTS) via NodeSource
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    # GitHub CLI
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y --no-install-recommends gh && \
    # AWS CLI v2 (ECS task role credentials auto-injected)
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscli.zip && \
    unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/awscli.zip /tmp/aws && \
    # Cleanup
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agent_runner.py /app/agent_runner.py

# CRITICAL: Non-root user (Claude Code refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash agent && chown -R agent:agent /app
USER agent

WORKDIR /app
ENTRYPOINT ["python", "agent_runner.py"]
```

**File:** `infra/improve-agent/requirements.txt`

```
boto3>=1.34
```

---

## 4. ECS Agent Python Runner

**File:** `infra/improve-agent/agent_runner.py`

This is the complete agent. Copy the full file from
`infra/improve-agent/agent_runner.py` in the Bill Review repo. Key sections:

### Configuration

```python
REPORT_ID           = os.environ.get("REPORT_ID", "")           # REQUIRED — passed by ECS override
DRY_RUN             = os.environ.get("DRY_RUN", "") == "1"      # Skip Claude/git for testing
AWS_REGION          = os.environ.get("AWS_REGION", "us-east-1")
DEBUG_TABLE         = os.environ.get("DEBUG_TABLE", "your-debug-table")
S3_BUCKET           = os.environ.get("S3_BUCKET", "your-bucket")
ANTHROPIC_SECRET    = os.environ.get("ANTHROPIC_SECRET", "improve-agent/anthropic-api-key")
GH_SECRET           = os.environ.get("GH_SECRET", "improve-agent/gh-token")
REPO_SLUG           = os.environ.get("REPO_SLUG", "yourorg/yourrepo")
EMAIL_SENDER        = os.environ.get("IMPROVE_EMAIL_SENDER", "noreply@yourdomain.com")
EMAIL_RECIPIENTS    = ["team@yourdomain.com"]
EMAIL_RECIPIENTS_PR = ["team@yourdomain.com", "managers@yourdomain.com"]
```

### Execution Flow (main function)

```
1. Read report from DynamoDB (REPORT_ID)
2. Update status → "Agent Running"
3. Fetch secrets from Secrets Manager:
   - ANTHROPIC_API_KEY from "improve-agent/anthropic-api-key"
   - GH_TOKEN from "improve-agent/gh-token"
4. Clone repo: git clone --depth=1 https://{TOKEN}@github.com/{SLUG}.git
5. Create branch: improve/{type}-{short_id}
6. Build prompt (type-specific instructions + title + desc + context + screenshots)
7. Run Claude Code:
   claude -p "{prompt}" --dangerously-skip-permissions --output-format json --model sonnet
8. Check for file changes:
   - If changes → commit → push → create PR via `gh pr create` → email success
   - If no changes + investigation keywords → email investigation findings
   - If no changes → email "no changes made"
9. Update DynamoDB with final status + PR URL + agent summary
```

### The Prompt Builder

The prompt sent to Claude includes:
- **Type-specific instruction header** (bug vs enhancement vs feature)
- **Title and description** from the user
- **Page URL** where the user was
- **Page context** (path, viewport, console errors, page errors)
- **Presigned screenshot URLs** (Claude Code can view images)
- **Rules:** "Do NOT deploy", "You MUST edit files", "Read CLAUDE.md first"
- **Investigation context** (optional — S3 stages, DynamoDB tables, Lambda functions, example AWS CLI commands for your app's infrastructure)

### How Claude Code is Invoked

```python
def run_claude(prompt: str, anthropic_key: str) -> dict:
    env = {"ANTHROPIC_API_KEY": anthropic_key}
    model = os.environ.get("CLAUDE_MODEL", "sonnet")
    result = subprocess.run(
        ["claude", "-p", prompt,
         "--dangerously-skip-permissions",
         "--output-format", "json",
         "--model", model],
        cwd="/tmp/repo",
        env={**os.environ, **env},
        capture_output=True, text=True,
        timeout=1200,  # 20 minute timeout
    )
    return json.loads(result.stdout)
```

Key flags:
- **`-p`**: Non-interactive mode (pass the prompt as a string)
- **`--dangerously-skip-permissions`**: Allow file edits without confirmation (requires non-root user)
- **`--output-format json`**: Machine-readable output with `result` field
- **`--model sonnet`**: Model selection (sonnet is fast + capable; opus for complex bugs)

---

## 5. AWS Infrastructure Setup

**File:** `infra/improve-agent/setup_infra.ps1`

Run once to create all AWS resources. Creates:

| Step | Resource | Name |
|------|----------|------|
| 1 | ECR Repository | `jrk-improve-agent` |
| 2 | CloudWatch Log Group | `/ecs/jrk-improve-agent` (30-day retention) |
| 3 | IAM Execution Role | `jrk-improve-agent-execution-role` (ECR pull + logs) |
| 4 | IAM Task Role | `jrk-improve-agent-task-role` (DDB + SES + S3 + Secrets) |
| 5 | Secrets Manager | `improve-agent/anthropic-api-key`, `improve-agent/gh-token` |
| 6 | ECS Cluster | `jrk-improve-agent` (Fargate) |
| 7 | Security Group | `jrk-improve-agent-sg` (outbound only, no inbound) |
| 8 | Subnets | Auto-detected from default VPC |
| 9 | ECS Task Definition | `jrk-improve-agent` (2 vCPU, 4 GB RAM) |

---

## 6. AWS Secrets

Two secrets in AWS Secrets Manager:

| Secret Name | Value | How to Get |
|-------------|-------|------------|
| `improve-agent/anthropic-api-key` | `sk-ant-api03-...` | [console.anthropic.com](https://console.anthropic.com/) → API Keys |
| `improve-agent/gh-token` | `ghp_...` | GitHub → Settings → Developer Settings → Personal Access Tokens (needs `repo` scope) |

**Set the real values:**
```bash
aws secretsmanager put-secret-value \
  --secret-id improve-agent/anthropic-api-key \
  --secret-string "sk-ant-api03-YOUR-KEY-HERE" \
  --region us-east-1

aws secretsmanager put-secret-value \
  --secret-id improve-agent/gh-token \
  --secret-string "ghp_YOUR-TOKEN-HERE" \
  --region us-east-1
```

---

## 7. Environment Variables

### On your web app (AppRunner / ECS / EC2)

Set these so the backend can trigger the Fargate agent:

```
IMPROVE_AGENT_CLUSTER  = jrk-improve-agent          # ECS cluster name
IMPROVE_AGENT_TASK_DEF = jrk-improve-agent           # Task definition family
IMPROVE_AGENT_SUBNETS  = subnet-abc123,subnet-def456 # Comma-separated
IMPROVE_AGENT_SG       = sg-0123456789abcdef0        # Security group ID
IMPROVE_EMAIL_SENDER   = noreply@yourdomain.com      # SES verified sender
DEBUG_TABLE            = jrk-bill-review-debug       # DynamoDB table name
```

If the `IMPROVE_AGENT_CLUSTER` env var is empty, the ECS trigger is silently skipped (safe default for dev).

### On the ECS Fargate task definition (set in task def JSON)

```
AWS_REGION    = us-east-1
DEBUG_TABLE   = jrk-bill-review-debug
S3_BUCKET     = jrk-analytics-billing
```

These are passed via `REPORT_ID` override at runtime (per-invocation):
```
REPORT_ID = <uuid from DynamoDB>
```

---

## 8. IAM Permissions

### Web App Role (AppRunner / ECS service)

Your existing web app needs these additional permissions:

```json
{
  "Effect": "Allow",
  "Action": "ecs:RunTask",
  "Resource": "arn:aws:ecs:us-east-1:ACCOUNT:task-definition/jrk-improve-agent"
},
{
  "Effect": "Allow",
  "Action": "iam:PassRole",
  "Resource": [
    "arn:aws:iam::ACCOUNT:role/jrk-improve-agent-execution-role",
    "arn:aws:iam::ACCOUNT:role/jrk-improve-agent-task-role"
  ]
}
```

Plus existing: `dynamodb:PutItem` on the debug table, `s3:PutObject` + `s3:GetObject` on the bucket, `ses:SendEmail`.

### ECS Task Role (what the agent container runs as)

```json
[
  { "Sid": "DynamoDB",       "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem"],
    "Resource": "arn:aws:dynamodb:REGION:ACCOUNT:table/DEBUG_TABLE" },
  { "Sid": "SES",            "Action": "ses:SendEmail", "Resource": "*",
    "Condition": {"StringEquals": {"ses:FromAddress": "noreply@yourdomain.com"}} },
  { "Sid": "S3Read",         "Action": ["s3:GetObject", "s3:ListBucket"],
    "Resource": ["arn:aws:s3:::BUCKET", "arn:aws:s3:::BUCKET/*"] },
  { "Sid": "SecretsManager", "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:improve-agent/*" },
  { "Sid": "CloudWatchLogs", "Action": ["logs:FilterLogEvents", "logs:DescribeLogStreams"],
    "Resource": "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/lambda/*" },
  { "Sid": "DDBReadAll",     "Action": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
    "Resource": "arn:aws:dynamodb:REGION:ACCOUNT:table/your-app-tables-*" }
]
```

### ECS Execution Role

Standard `AmazonECSTaskExecutionRolePolicy` + Secrets Manager access for injecting secrets.

---

## 9. Deployment Checklist

### First-time setup

- [ ] Create DynamoDB table (`report_id` as partition key, type `S`)
- [ ] Run `setup_infra.ps1` to create all AWS resources
- [ ] Set real values in Secrets Manager (Anthropic API key + GitHub PAT)
- [ ] Build and push Docker image:
  ```bash
  aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.us-east-1.amazonaws.com
  docker build -t jrk-improve-agent:latest infra/improve-agent/
  docker tag jrk-improve-agent:latest ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/jrk-improve-agent:latest
  docker push ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/jrk-improve-agent:latest
  ```
- [ ] Verify SES sender email is verified in your region
- [ ] Set the 4 `IMPROVE_AGENT_*` env vars on your web app
- [ ] Add `ecs:RunTask` + `iam:PassRole` to your web app's IAM role
- [ ] Add the IMPROVE button HTML/CSS/JS to your template(s)
- [ ] Add the 3 FastAPI endpoints to your backend
- [ ] Customize `INVESTIGATION_CONTEXT` in `agent_runner.py` for YOUR app's infrastructure

### Updating the agent

```bash
docker build -t jrk-improve-agent:latest infra/improve-agent/
docker tag jrk-improve-agent:latest ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/jrk-improve-agent:latest
docker push ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/jrk-improve-agent:latest
```

No ECS task definition update needed — it uses `:latest` tag.

### Testing

Set `DRY_RUN=1` on the ECS task to build the prompt without running Claude or git:
```bash
aws ecs run-task \
  --cluster jrk-improve-agent \
  --task-definition jrk-improve-agent \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-abc],securityGroups=[sg-xyz],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"improve-agent","environment":[{"name":"REPORT_ID","value":"YOUR-REPORT-ID"},{"name":"DRY_RUN","value":"1"}]}]}'
```

Check CloudWatch logs at `/ecs/jrk-improve-agent` for output.

---

## Customization Checklist (for a new app)

When copying this to a new project, update these values:

| What | Where | Example |
|------|-------|---------|
| DynamoDB table name | Backend env + agent env | `my-app-debug` |
| S3 bucket | Backend env + agent env | `my-app-assets` |
| Email sender | Backend + agent | `noreply@myapp.com` |
| Email recipients | Backend + agent | `["team@myapp.com"]` |
| GitHub repo slug | Agent env `REPO_SLUG` | `myorg/my-app` |
| Agent name | `setup_infra.ps1` `$AGENT_NAME` | `my-app-improve-agent` |
| `INVESTIGATION_CONTEXT` | `agent_runner.py` | Your app's S3 paths, DDB tables, Lambda functions |
| Claude model | Agent env `CLAUDE_MODEL` | `sonnet` (fast) or `opus` (thorough) |
| CLAUDE.md | Repo root | Your project conventions — the agent reads this first |
| Task CPU/Memory | Task definition | `2048` CPU / `4096` MB is good for most |
