#!/usr/bin/env python3
"""
Script to add IMPROVE button, modal, and functionality to all HTML templates.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path("bill_review_app/templates")

# Files to process (exclude login.html, landing.html, review.html, invoices.html as they're done)
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
]

# CSS to add before </style>
MODAL_CSS = """    .improve-modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:10000;align-items:center;justify-content:center}
    .improve-modal-overlay.show{display:flex}
    .improve-modal-box{background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.3);max-width:500px;width:90%;padding:24px}
    .improve-modal-box h2{margin:0 0 16px 0}
    .improve-modal-box label{display:block;margin-bottom:4px;font-weight:600;font-size:13px}
    .improve-modal-box input,.improve-modal-box textarea{width:100%;padding:10px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:12px;font-family:inherit}
    .improve-modal-box textarea{min-height:100px;resize:vertical}
    .improve-modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
    .improve-toast{position:fixed;top:20px;right:20px;background:#111827;color:#fff;padding:12px 16px;border-radius:10px;box-shadow:0 8px 20px rgba(0,0,0,.25);z-index:10001;opacity:0;transition:opacity .3s}
    .improve-toast.show{opacity:.96}
    .improve-toast.ok{background:#0f766e}
    .improve-toast.err{background:#b91c1c}
"""

# Modal HTML to add after header
MODAL_HTML = """
  <div id="improveToast" class="improve-toast"></div>

  <div id="improveModal" class="improve-modal-overlay">
    <div class="improve-modal-box">
      <h2>Report Bug or Enhancement</h2>
      <label for="reportTitle">Title</label>
      <input type="text" id="reportTitle" placeholder="Brief summary of issue or enhancement" />
      <label for="reportDesc">Description</label>
      <textarea id="reportDesc" placeholder="Detailed description of what you'd like improved"></textarea>
      <div class="improve-modal-actions">
        <button class="btn secondary" id="cancelReport">Cancel</button>
        <button class="btn" id="submitReport">Submit</button>
      </div>
    </div>
  </div>"""

# JavaScript to add before </script> or before </body>
MODAL_JS = """
    // IMPROVE modal functionality
    (function(){
      function showImproveToast(msg, type){
        const t = document.getElementById('improveToast');
        t.textContent = msg;
        t.className = 'improve-toast ' + (type === 'ok' ? 'ok' : 'err');
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 2500);
      }

      const modal = document.getElementById('improveModal');
      const improveBtn = document.getElementById('improveBtn');
      const cancelBtn = document.getElementById('cancelReport');
      const submitBtn = document.getElementById('submitReport');
      const titleInput = document.getElementById('reportTitle');
      const descInput = document.getElementById('reportDesc');

      if (!improveBtn) return; // Guard if button not found

      improveBtn.addEventListener('click', () => {
        modal.classList.add('show');
        titleInput.focus();
      });

      cancelBtn.addEventListener('click', () => {
        modal.classList.remove('show');
        titleInput.value = '';
        descInput.value = '';
      });

      modal.addEventListener('click', (e) => {
        if (e.target === modal) {
          modal.classList.remove('show');
          titleInput.value = '';
          descInput.value = '';
        }
      });

      submitBtn.addEventListener('click', async () => {
        const title = titleInput.value.trim();
        const description = descInput.value.trim();

        if (!title || !description) {
          showImproveToast('Please fill in both title and description', 'err');
          return;
        }

        try {
          const response = await fetch('/api/debug/report', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              title: title,
              description: description,
              page_url: window.location.href
            })
          });

          if (!response.ok) {
            throw new Error('Failed to submit report');
          }

          showImproveToast('Report submitted successfully', 'ok');
          modal.classList.remove('show');
          titleInput.value = '';
          descInput.value = '';
        } catch (e) {
          showImproveToast('Error submitting report', 'err');
        }
      });
    })();"""


def add_improve_to_template(file_path: Path):
    """Add IMPROVE button, modal, and JavaScript to a template file."""
    print(f"Processing {file_path.name}...")

    content = file_path.read_text(encoding='utf-8')

    # Check if already processed
    if 'improveBtn' in content or 'IMPROVE' in content:
        print(f"  [SKIP] {file_path.name} - already has IMPROVE button")
        return

    # 1. Add CSS before </style>
    if '</style>' in content:
        content = content.replace('  </style>', f'{MODAL_CSS}  </style>', 1)
    else:
        print(f"  [WARN] No </style> tag found in {file_path.name}")

    # 2. Add IMPROVE button to header
    # Find the logout section and add IMPROVE button before it
    header_patterns = [
        # Pattern 1: <div class="logout">
        (r'(<div class="logout">)', r'<button class="btn secondary" id="improveBtn">IMPROVE</button>\n      \1'),
        # Pattern 2: <div> with buttons/links before </header>
        (r'(<div>\s*<a class="btn)', r'<div>\n      <button class="btn secondary" id="improveBtn">IMPROVE</button>\n      <a class="btn'),
        # Pattern 3: Just before </header> if no other pattern matches
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

    # 4. Add JavaScript
    # Try to add before closing </script> tag, or before </body> if no script
    if '</script>' in content:
        # Add before the last </script>
        parts = content.rsplit('</script>', 1)
        content = parts[0] + MODAL_JS + '\n  </script>' + parts[1]
    elif '</body>' in content:
        # Add a script section before </body>
        content = content.replace('</body>', f'  <script>{MODAL_JS}\n  </script>\n</body>', 1)
    else:
        print(f"  [WARN] No </script> or </body> tag found in {file_path.name}")

    # Write back
    file_path.write_text(content, encoding='utf-8')
    print(f"  [OK] Updated {file_path.name}")


def main():
    print("Adding IMPROVE functionality to templates...")
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
