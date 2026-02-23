# Legal Bill Parser (Desktop)

Packaged desktop app and build artifacts for parsing legal bills.

## Contents

- `GEMINI_PDF_PARSER.py` — entry point for the desktop workflow.
- `build/`, `dist/` — PyInstaller artifacts and final executable(s).
- `.spec` — PyInstaller spec file.

## Build

Rebuild the desktop app with PyInstaller:
```powershell
pyinstaller --noconfirm --clean --onefile --name LegalBillParser GEMINI_PDF_PARSER.py
```

Outputs land in `dist/`.
