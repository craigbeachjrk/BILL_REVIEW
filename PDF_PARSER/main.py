import os
import sys
import json
import argparse
from utility_bill_parser import UtilityBillParser

def parse_args():
    parser = argparse.ArgumentParser(description="Parse utility bill PDFs")
    parser.add_argument(
        "pdf_path", 
        help="Path to the PDF file or directory containing PDF files"
    )
    parser.add_argument(
        "--output", "-o", 
        help="Output directory for JSON files (default: same as input)",
        default=None
    )
    parser.add_argument(
        "--model", "-m",
        help="Path to the local Deepseek model",
        default="models/deepseek-r1-8b"
    )
    return parser.parse_args()

def process_file(parser, pdf_path, output_dir=None):
    """Process a single PDF file"""
    try:
        print(f"Processing {pdf_path}...")
        result = parser.parse_bill(pdf_path)
        
        # Determine output path
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            base_name = os.path.basename(pdf_path).replace(".pdf", ".json")
            output_path = os.path.join(output_dir, base_name)
        else:
            output_path = pdf_path.replace(".pdf", ".json")
            
        # Save result to JSON file
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
            
        print(f"Results saved to {output_path}")
        return True
    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")
        return False

def process_directory(parser, directory, output_dir=None):
    """Process all PDF files in a directory"""
    success_count = 0
    failure_count = 0
    
    for filename in os.listdir(directory):
        if filename.lower().endswith('.pdf'):
            pdf_path = os.path.join(directory, filename)
            if process_file(parser, pdf_path, output_dir):
                success_count += 1
            else:
                failure_count += 1
                
    print(f"Processed {success_count + failure_count} files: {success_count} successful, {failure_count} failed")

def main():
    args = parse_args()
    
    # Initialize parser
    parser = UtilityBillParser(model_path=args.model)
    
    # Process input path (file or directory)
    if os.path.isfile(args.pdf_path):
        process_file(parser, args.pdf_path, args.output)
    elif os.path.isdir(args.pdf_path):
        process_directory(parser, args.pdf_path, args.output)
    else:
        print(f"Error: {args.pdf_path} is not a valid file or directory")
        sys.exit(1)

if __name__ == "__main__":
    main()
