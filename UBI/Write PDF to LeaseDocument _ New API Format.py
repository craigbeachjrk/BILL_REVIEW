import os
import re
import time
import json
import requests
import base64
from datetime import date

# -----------------------------
# 1) Global/Config Variables
# -----------------------------
CLIENT_ID = 'ee34f50b-08e0-472b-905b-de6bc0282f6b'
TENANT_ID = '4ef87a9f-783f-4d83-8f85-859338ad4346'
CLIENT_SECRET = 'YOUR_CLIENT_SECRET_HERE'
API_KEY = '288f3174-2ec2-48e5-8f31-742ec278e53b'

# username = 'jrk_utility_billing_api_3422@jrkpropertyholdingsentratacore'
# password = 'YOUR_PASSWORD_HERE'
# credentials = f"{username}:{password}"
# encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

# Entrata URL (endpoint)
# sendleasedoc_url = 'https://jrkpropertyholdingsentratacore.entrata.com/api/leases'
sendleasedoc_url = 'https://apis.entrata.com/ext/orgs/jrkpropertyholdingsentratacore/v1/leases'

# The folder with the PDF files
# OUTPUT_FOLDER = r'P:\Utility Billing\_Entrata\2025\2025-03 March\Invoices\Entrata Core Invoices'
#OUTPUT_FOLDER = r'P:\Utility Billing\_Entrata\2025\2025-03 March\Invoices\CHC and DUO'
#OUTPUT_FOLDER = r'P:\Utility Billing\Z-Entrata_EOM\01-25\TRANSITIONAL\INVOICES_POSTED_TO_RESIDENT_PORTAL'
# OUTPUT_FOLDER = r'P:\Utility Billing\_Entrata\2025\2025-04 April\Invoices\To Post'
OUTPUT_FOLDER = r'P:\Utility Billing\_Entrata\2025\2025-06 June\Invoices\Files to Upload - WS Correction'

# This stays constant or changes as needed
date_append_for_id = '20250201'
lease_file_type = 'CI'
file_id = '3895272'

# ------------------------------------------------------------------
# 2) Iterate Through All PDF Files in the OUTPUT_FOLDER
# ------------------------------------------------------------------
def main():
    counter = 0
        # ─────────────────────────────────────────────────────────────
    # ask the user how many PDFs to process (0 ⇒ no limit)
    # ─────────────────────────────────────────────────────────────
    max_count = int(
        input(
            "Enter the maximum number of PDFs to process "
            "(0 for no limit): "
        )
            )
    # Go through each item in the folder
    for filename in os.listdir(OUTPUT_FOLDER):

        # ----------  STOP if we've hit the user-defined limit  ----------
        if max_count and counter >= max_count:
            print(f"\nReached the limit of {max_count} files — stopping.\n")
            break
        # ----------------------------------------------------------------
        # We only want PDF files
        if filename.lower().endswith('.pdf'):
            pdf_filepath = os.path.join(OUTPUT_FOLDER, filename)

            # Example filename pattern:
            # UBI-100056624-16110065-BOJ-1103-MAJ-12_01_2024.pdf
            # We want the second item to be p_i (100056624)
            # and the third item to be lease_id (16110065)

            # Split on '-'
            parts = filename.split('-')
            # Safety check that we have enough parts
            if len(parts) < 3:
                print(f"Skipping {filename}: not enough hyphen-separated parts")
                continue

            # Extract p_i and lease_id
            # parts[0] => "UBI"
            # parts[1] => "100056624" (this is p_i)
            # parts[2] => "16110065"  (this is lease_id)
            p_i_str     = parts[1]
            lease_id_str = parts[2]

            # Convert them to int if needed:
            try:
                p_i = int(p_i_str)
                lease_id = int(lease_id_str)
                counter += 1
            except ValueError:
                print(f"Skipping {filename}: p_i or lease_id not numeric.")
                continue
            
            
            print(f"\nProcessing file: {filename}. Counter: {counter}")
            print(f"Extracted p_i={p_i}, lease_id={lease_id}")

            # 3) Construct the requestBody for the API
            #    We'll use today's date as "request_id":
            today_str = str(date.today())

            sendleasedoc_request_body = f'''
            {{
            

            
                "auth": {{
                    "type": "apikey"
                }},
                "requestId": "{today_str}",
                "method": {{
                    "name": "sendLeaseDocuments",
                    "params": {{
                        "propertyId": "{p_i}",
                        "leaseId": "{lease_id}",
                        "files": {{
                            "file": [
                                {{
                                    "fileName": "{filename}",
                                    "leaseFileType": "{lease_file_type}",
                                    "fileTypeId": "{file_id}",
                                    "isShowInResidentPortal": "1"
                                }}
                            ]
                        }}
                    }}
                }}
            }}
            '''

            # 4) Prepare headers for the request
            sendleasedoc_request_header = {
                'X-API-Key': API_KEY,
                'X-Send-Pagination-Links': '0'
                # We do NOT manually set "Content-Type", requests will handle
                # that since we're sending multipart/form-data
            }

            # 5) Send the file and JSON body
            with open(pdf_filepath, 'rb') as f:
                files = {
                    # "file1" matches the Java snippet's usage
                    "file1": (filename, f, "application/octet-stream")
                }
                data = {
                    "requestBody": sendleasedoc_request_body,
                    "requestContentType": "APPLICATION/JSON; CHARSET=UTF-8"
                }

                try:
                    response = requests.post(
                        sendleasedoc_url,
                        headers=sendleasedoc_request_header,
                        files=files,   # This is the binary PDF
                        data=data      # Additional form fields
                    )
                except Exception as e:
                    print(f"Error posting {filename} to API: {e}")
                    continue

                # Delay if needed between postings
                time.sleep(.9)

                # 6) Print out the API response
                try:
                    response_json = response.json()
                    print("JSON Response:")
                    print(json.dumps(response_json, indent=4))
                except json.JSONDecodeError:
                    # Not JSON, just print raw text
                    print("Non-JSON response:")
                    print(response.text)

    print("\nDone iterating through PDFs.")


if __name__ == "__main__":
    main()
