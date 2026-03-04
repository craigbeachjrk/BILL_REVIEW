#!/usr/bin/env python3
"""
Script to add/upgrade IMPROVE button, modal, and functionality to all HTML templates.

Run this script after updating the MODAL_CSS / MODAL_HTML / MODAL_JS constants below.
It will strip any existing IMPROVE code from each template and re-inject the latest version.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path("templates")

# Every template that should have the IMPROVE button
FILES_TO_PROCESS = [
    "input.html",
    "post.html",
    "track.html",
    "config.html",
    "ubi.html",
    "uom_mapping.html",
    "ap_mapping.html",
    "ap_team.html",
    "ubi_mapping.html",
    "day.html",
    "index.html",
    "config_menu.html",
    "debug.html",
    "landing.html",
    "review.html",
    "invoices.html",
    "billback.html",
    "billback_summary.html",
    "charge_codes.html",
    "config_old.html",
    "failed.html",
    "history.html",
    "metrics.html",
]

# ── CSS ──────────────────────────────────────────────────────────────────
MODAL_CSS = """    /* ===IMPROVE-CSS-START=== */
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
    .improve-type-group input[type=radio]:checked+span{background:transparent}
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
    .improve-ss-uploading{opacity:.5}
    /* ===IMPROVE-CSS-END=== */
"""

# ── HTML ─────────────────────────────────────────────────────────────────
MODAL_HTML = """
  <!-- ===IMPROVE-HTML-START=== -->
  <div id="improveToast" class="improve-toast"></div>

  <div id="improveModal" class="improve-modal-overlay">
    <div class="improve-modal-box">
      <h2>Report an Issue or Idea</h2>
      <div class="improve-path-badge" id="improvePagePath"></div>

      <div class="improve-type-group" id="improveTypeGroup">
        <label class="sel-bug" id="improveTypeBugLbl"><input type="radio" name="improveType" value="bug" checked /><span>Bug</span></label>
        <label id="improveTypeEnhLbl"><input type="radio" name="improveType" value="enhancement" /><span>Enhancement</span></label>
        <label id="improveTypeFeatLbl"><input type="radio" name="improveType" value="feature" /><span>Feature Request</span></label>
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
  <!-- ===IMPROVE-HTML-END=== -->"""

# ── JavaScript ───────────────────────────────────────────────────────────
MODAL_JS = """
    // ===IMPROVE-JS-START===
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
        typeGroup.querySelectorAll('label').forEach(function(lbl){
          lbl.className = '';
        });
        if (checked) {
          const v = checked.value;
          checked.parentElement.className = 'sel-' + v;
        }
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
        _tempReportId = crypto.randomUUID ? crypto.randomUUID() : ('xx-' + Date.now() + '-' + Math.random().toString(36).slice(2));
        refreshPathBadge();
        modal.classList.add('show');
        titleInput.focus();
      });

      cancelBtn.addEventListener('click', function(){
        modal.classList.remove('show');
        resetModal();
      });

      modal.addEventListener('click', function(e){
        if (e.target === modal){
          modal.classList.remove('show');
          resetModal();
        }
      });

      // ---------- screenshot helpers ----------
      function addScreenshotFile(file){
        if (!file || !file.type.startsWith('image/')) return;
        if (_ssKeys.length + _ssUploading >= 5){
          showImproveToast('Max 5 screenshots', 'err');
          return;
        }
        if (file.size > 5 * 1024 * 1024){
          showImproveToast('Image exceeds 5 MB limit', 'err');
          return;
        }

        const reader = new FileReader();
        reader.onload = function(){
          const dataUrl = reader.result;
          const contentType = file.type || 'image/png';

          // Create thumbnail immediately
          const thumb = document.createElement('div');
          thumb.className = 'improve-ss-thumb improve-ss-uploading';
          const img = document.createElement('img');
          img.src = dataUrl;
          thumb.appendChild(img);
          const removeBtn = document.createElement('button');
          removeBtn.className = 'ss-remove';
          removeBtn.textContent = '\\u00d7';
          thumb.appendChild(removeBtn);
          ssStrip.appendChild(thumb);

          _ssUploading++;

          // Upload
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
            } else {
              thumb.remove();
              showImproveToast(data.error || 'Upload failed', 'err');
            }
          })
          .catch(function(){
            _ssUploading--;
            thumb.remove();
            showImproveToast('Screenshot upload failed', 'err');
          });
        };
        reader.readAsDataURL(file);
      }

      // Paste handler — only active when modal is open
      document.addEventListener('paste', function(e){
        if (!modal.classList.contains('show')) return;
        const items = (e.clipboardData || {}).items || [];
        for (let i = 0; i < items.length; i++){
          if (items[i].type.indexOf('image') !== -1){
            e.preventDefault();
            addScreenshotFile(items[i].getAsFile());
            return;
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
        e.preventDefault();
        ssArea.classList.remove('drag');
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
        // Capture visible error messages on the page
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
        if (submitBtn.disabled) return;
        var title = titleInput.value.trim();
        var description = descInput.value.trim();

        if (!title || !description){
          showImproveToast('Please fill in title and description', 'err');
          return;
        }
        if (_ssUploading > 0){
          showImproveToast('Screenshots still uploading...', 'err');
          return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = 'Submitting...';

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
        } finally {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Submit';
        }
      });
    })();
    // ===IMPROVE-JS-END==="""


# ═══════════════════════════════════════════════════════════════════════════
# Template processing
# ═══════════════════════════════════════════════════════════════════════════

def _strip_old_improve(content: str) -> str:
    """Remove any previously-injected IMPROVE code from a template.

    Uses deterministic comment markers (===IMPROVE-*-START/END===) for reliable
    stripping. Falls back to pattern-based matching for v1 code that lacks markers.
    """

    # 1. CSS — try marker-based first, fall back to v1/v2 pattern
    content = re.sub(
        r'\n?\s*/\* ===IMPROVE-CSS-START=== \*/.*?/\* ===IMPROVE-CSS-END=== \*/\n?',
        '\n', content, count=1, flags=re.DOTALL
    )
    # v2-without-markers fallback: "/* IMPROVE modal styles */" through ".improve-ss-uploading{...}"
    content = re.sub(
        r'\n?\s*/\* IMPROVE modal styles \*/.*?\.improve-ss-uploading\{[^}]*\}\n?',
        '\n', content, count=1, flags=re.DOTALL
    )
    # v1 fallback (no markers, no screenshot CSS): from .improve-modal-overlay{ to .improve-toast.err{...}
    content = re.sub(
        r'\n?\s*\.improve-modal-overlay\{[^}]*\}.*?\.improve-toast\.err\{[^}]*\}\n?',
        '\n', content, count=1, flags=re.DOTALL
    )

    # 2. HTML — try marker-based first, fall back to v1 pattern
    content = re.sub(
        r'\n?\s*<!-- ===IMPROVE-HTML-START=== -->.*?<!-- ===IMPROVE-HTML-END=== -->\n?',
        '', content, count=1, flags=re.DOTALL
    )
    # v1 fallback: from <div id="improveToast" to the modal's closing structure
    content = re.sub(
        r'\n?\s*<div id="improveToast"[^>]*>.*?id="submitReport"[^>]*>Submit</button>\s*</div>\s*</div>\s*</div>',
        '', content, count=1, flags=re.DOTALL
    )

    # 3. JS — try marker-based first, fall back to v1 pattern
    content = re.sub(
        r'\n?\s*// ===IMPROVE-JS-START===.*?// ===IMPROVE-JS-END===\n?',
        '', content, count=1, flags=re.DOTALL
    )
    # v1 fallback: from "// IMPROVE modal" to the IIFE close with specific var name
    content = re.sub(
        r'\n?\s*// IMPROVE modal[^\n]*\n\s*\(function\(\)\{.*?showImproveToast.*?\}\)\(\);\n?',
        '', content, count=1, flags=re.DOTALL
    )

    # 4. Remove IMPROVE button (will be re-added)
    content = re.sub(
        r'\s*<button class="btn secondary" id="improveBtn">IMPROVE</button>\n?',
        '', content
    )

    return content


def add_improve_to_template(file_path: Path):
    """Add or upgrade IMPROVE button, modal, and JavaScript in a template file."""
    print(f"Processing {file_path.name}...")

    content = file_path.read_text(encoding='utf-8')

    # Strip any existing IMPROVE code first
    content = _strip_old_improve(content)

    # 1. Add CSS before </style>
    if '</style>' in content:
        content = content.replace('  </style>', f'{MODAL_CSS}  </style>', 1)
    else:
        print(f"  [WARN] No </style> tag found in {file_path.name}")

    # 2. Add IMPROVE button to header
    header_patterns = [
        (r'(<div class="logout">)', r'<button class="btn secondary" id="improveBtn">IMPROVE</button>\n      \1'),
        (r'(<div[^>]*>\s*<a class="btn)', r'<div>\n      <button class="btn secondary" id="improveBtn">IMPROVE</button>\n      <a class="btn'),
        (r'(</header>)', r'<button class="btn secondary" id="improveBtn">IMPROVE</button>\n  \1'),
    ]

    header_added = False
    for pattern, replacement in header_patterns:
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content, count=1)
            header_added = True
            break

    if not header_added:
        print(f"  [WARN] Could not find header pattern in {file_path.name}")

    # 3. Add modal HTML after </header>
    if '</header>' in content:
        content = content.replace('</header>', f'</header>{MODAL_HTML}', 1)
    else:
        print(f"  [WARN] No </header> tag found in {file_path.name}")

    # 4. Add JavaScript before last </script> or before </body>
    if '</script>' in content:
        parts = content.rsplit('</script>', 1)
        content = parts[0] + MODAL_JS + '\n  </script>' + parts[1]
    elif '</body>' in content:
        content = content.replace('</body>', f'  <script>{MODAL_JS}\n  </script>\n</body>', 1)
    else:
        print(f"  [WARN] No </script> or </body> tag found in {file_path.name}")

    file_path.write_text(content, encoding='utf-8')
    print(f"  [OK] Updated {file_path.name}")


def main():
    print("Adding/upgrading IMPROVE functionality in templates...")
    print()

    for filename in FILES_TO_PROCESS:
        file_path = TEMPLATES_DIR / filename
        if file_path.exists():
            try:
                add_improve_to_template(file_path)
            except Exception as e:
                print(f"  [ERROR] processing {filename}: {e}")
        else:
            print(f"  [WARN] File not found: {filename}")

    print()
    print("Done!")


if __name__ == "__main__":
    main()
