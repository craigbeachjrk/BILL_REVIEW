# Utility Bill PDF Parser

This project provides a tool to parse utility bill PDFs using the Deepseek language model deployed locally. It extracts structured data from utility invoices including account information, service details, consumption data, and charges.

## Setup

### 1. Create and activate a virtual environment

```cmd
python -m venv venv
venv\Scripts\activate.bat
```

### 2. Install dependencies

```cmd
pip install -r requirements.txt
```

### 3. Download the Deepseek model

```cmd
python download_model.py
```

This will download the Deepseek-R1-Distill-Llama-8B model to the `models/deepseek-r1-8b` directory.

## Usage

### Process a single PDF file

```cmd
python main.py path\to\your\bill.pdf
```

### Process all PDFs in a directory

```cmd
python main.py path\to\your\bills\directory
```

### Specify an output directory

```cmd
python main.py path\to\your\bill.pdf --output path\to\output\directory
```

### Use a different model

```cmd
python main.py path\to\your\bill.pdf --model path\to\your\model
```

## Understanding Python Virtual Environments

Python virtual environments are isolated Python environments that allow you to install and manage packages for specific projects without affecting your system-wide Python installation.

### Why Use Virtual Environments?

1. **Dependency Isolation**: Different projects can use different versions of the same package without conflicts.
2. **Clean Environment**: Start with only the packages you need, avoiding bloat.
3. **Reproducibility**: Makes it easier to recreate the same environment on different machines.
4. **No Admin Rights Required**: Install packages without system administrator privileges.

### How Virtual Environments Work

When you create a virtual environment:

1. Python copies a minimal Python interpreter into a new folder (e.g., `venv` folder)
2. It creates isolated `site-packages` directories where packages will be installed
3. It creates activation scripts that modify your shell's environment variables

### Virtual Environment Commands in Windows

**Create a virtual environment**:
```cmd
python -m venv venv_name
```

**Activate a virtual environment**:
```cmd
venv_name\Scripts\activate.bat
```
In PowerShell:
```powershell
.\venv_name\Scripts\Activate.ps1
```

**Deactivate a virtual environment**:
```cmd
deactivate
```

**Check installed packages**:
```cmd
pip list
```

**Install packages**:
```cmd
pip install package_name
```

**Create requirements file**:
```cmd
pip freeze > requirements.txt
```

**Install from requirements file**:
```cmd
pip install -r requirements.txt
```

## Output Format

The parser extracts the following information from utility bills:

- Bill to name (first and second line)
- Account number
- Service address
- Bill period (start and end dates)
- Utility type
- Line items (description, consumption amount, unit, charge)
- Bill date
- Due date
- Total amount

The output is saved as a JSON file with the same name as the input PDF.

## Components

- `text_extractor.py`: Extracts text from PDF files using pdfplumber with OCR fallback
- `utility_bill_parser.py`: Parses the extracted text using the Deepseek model
- `main.py`: Command-line interface for the parser
- `download_model.py`: Downloads the Deepseek model from Hugging Face

## Requirements

- Python 3.8+
- PyTorch
- Transformers
- pdfplumber
- pytesseract (for OCR fallback)
