import pdfplumber
import os
import pytesseract
from PIL import Image

def extract_text(path: str) -> str:
    """
    Extract text from a PDF file using pdfplumber.
    Falls back to OCR using pytesseract if text extraction fails.
    
    Args:
        path: Path to the PDF file
        
    Returns:
        Extracted text as a string
    """
    text = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
                else:  # scanned page → fallback OCR
                    img = page.to_image(resolution=300).original
                    text.append(pytesseract.image_to_string(img, lang="eng"))
        return "\n".join(text)
    except Exception as e:
        print(f"Error extracting text from {path}: {e}")
        return ""
