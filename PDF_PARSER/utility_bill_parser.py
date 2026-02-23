import json
import re
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from text_extractor import extract_text

class UtilityBillParser:
    def __init__(self, model_path="models/deepseek-r1-8b"):
        """
        Initialize the utility bill parser with a local Deepseek model
        
        Args:
            model_path: Path to the local Deepseek model
        """
        self.model_path = model_path
        self.tokenizer = None
        self.model = None
        self.generator = None
        self.load_model()
        
    def load_model(self):
        """Load the Deepseek model and tokenizer"""
        print(f"Loading model from {self.model_path}...")
        
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path, use_fast=True)
        
        # Use appropriate dtype based on available hardware
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path, 
            device_map="auto",
            torch_dtype=dtype
        )
        
        self.generator = pipeline(
            "text-generation", 
            model=self.model, 
            tokenizer=self.tokenizer,
            max_new_tokens=1024,  # Increased for complex invoices
            temperature=0.0  # Deterministic output
        )
        print("Model loaded successfully!")
        
    def parse_bill(self, pdf_path):
        """
        Parse a utility bill PDF and extract structured data
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Structured data as a dictionary
        """
        # Extract text from PDF
        text = extract_text(pdf_path)
        
        # Define the schema for utility bills based on the columns identified
        schema = """
        Return ONLY valid JSON with these fields extracted from the utility bill:
        {
          "bill_to_name": {
            "first_line": str,
            "second_line": str|null
          },
          "account_number": str,
          "service_address": str,
          "bill_period": {
            "start": str,  // Format: YYYY-MM-DD
            "end": str     // Format: YYYY-MM-DD
          },
          "utility_type": str,
          "line_items": [
            {
              "description": str,
              "consumption_amount": float|null,
              "unit": str|null,
              "charge": float
            }
          ],
          "bill_date": str,  // Format: YYYY-MM-DD
          "due_date": str,   // Format: YYYY-MM-DD
          "total_amount": float
        }
        """
        
        # Create prompt for the model
        prompt = f"""You are an expert utility bill parser.
        
{schema}

Extract the information from this utility bill PDF text:

```
{text}
```

Return ONLY the JSON object with no additional text or explanation.
"""
        
        # Generate response
        try:
            raw_response = self.generator(prompt)[0]["generated_text"]
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                data = json.loads(json_str)
                return data
            else:
                raise ValueError("No JSON found in model response")
                
        except Exception as e:
            print(f"Error parsing bill: {e}")
            return {"error": str(e)}
