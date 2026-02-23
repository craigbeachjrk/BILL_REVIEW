#!/usr/bin/env python3

import os
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv
from pathlib import Path
import shutil
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- CONFIGURATION ---
# Load environment variables from .env file
load_dotenv()



# Define directories relative to the script's location
# SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = Path(r"C:\Users\cbeach\Desktop\Local Automation\Bill_Parser_Test\SECOND_TEST_SET")
PARSED_DIR = SCRIPT_DIR / "_PARSED_OUTPUTS"
INSERTED_DIR = SCRIPT_DIR / "_INSERTED_INTO_DATABASE"

# Snowflake connection details (customize as needed)
SNOWFLAKE_USER = "JRK_READ_WRITE"  # IMPORTANT: This user needs INSERT rights
SNOWFLAKE_ACCOUNT = "EMC25361.us-east-1"
SNOWFLAKE_WAREHOUSE = "COMPUTE_WH"
SNOWFLAKE_DATABASE = "LAITMAN"
SNOWFLAKE_SCHEMA = "BILLPARSER" # IMPORTANT: This schema must exist
SNOWFLAKE_ROLE = "LAITMAN_RW" # IMPORTANT: This role needs USAGE rights on DB/Schema
SNOWFLAKE_TABLE = "UTILITY_BILL_DATA_RAW_V2"

def connect_to_snowflake():
    """Establishes a connection to Snowflake using key-pair authentication."""
    try:
        key_path = os.getenv("PRIV_KEY_FILE")
        key_password = os.getenv("PRIV_KEY_FILE_PWD")

        if not key_path or not os.path.exists(key_path):
            raise FileNotFoundError(f"Private key file not found at path: {key_path}. Check your .env file.")
        if not key_password:
            print("[WARN] Private key password is not set in .env file.")

        with open(key_path, "rb") as f:
            pk = serialization.load_pem_private_key(
                f.read(),
                password=key_password.encode() if key_password else None,
            )

        private_key = pk.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        print(f"Connecting to Snowflake account: {SNOWFLAKE_ACCOUNT}...")
        conn = snowflake.connector.connect(
            user=SNOWFLAKE_USER,
            account=SNOWFLAKE_ACCOUNT,
            private_key=private_key,
            warehouse=SNOWFLAKE_WAREHOUSE,
            database=SNOWFLAKE_DATABASE,
            schema=SNOWFLAKE_SCHEMA,
            role=SNOWFLAKE_ROLE,
        )
        print("Snowflake connection successful.")
        return conn
    except Exception as e:
        print(f"\n[ERROR] Failed to connect to Snowflake: {e}")
        return None

def main():
    """Main function to process and upload TSV files to Snowflake."""
    print("--- Starting Snowflake Upload Process ---")
    
    # Ensure directories exist
    PARSED_DIR.mkdir(exist_ok=True)
    INSERTED_DIR.mkdir(exist_ok=True)

    # Get list of TSV files to process
    tsv_files = list(PARSED_DIR.glob("*.tsv"))
    if not tsv_files:
        print(f"No .tsv files found in '{PARSED_DIR.name}'. Exiting.")
        return

    print(f"Found {len(tsv_files)} TSV file(s) to process.")

    conn = connect_to_snowflake()
    if not conn:
        print("Aborting due to connection failure.")
        return

    success_count = 0
    fail_count = 0

    try:
        for file_path in tsv_files:
            try:
                print(f"\nProcessing file: {file_path.name}")
                
                # Read the TSV file
                df = pd.read_csv(file_path, sep='\t', header=0)

                # Add load timestamp in Pacific Time
                df['LOAD_TIMESTAMP_PT'] = datetime.now(ZoneInfo("America/Los_Angeles"))

                # Ensure column names match Snowflake table (case-sensitive)
                # The write_pandas function handles mapping by default if auto_create_table=False

                print(f"Inserting {len(df)} rows into {SNOWFLAKE_TABLE}...")
                write_pandas(
                    conn=conn,
                    df=df,
                    table_name=SNOWFLAKE_TABLE,
                    auto_create_table=False, # We've already created it
                    quote_identifiers=True # Handles spaces in column names
                )

                print(f"Successfully inserted data from {file_path.name}.")
                
                # Move the file to the inserted directory
                shutil.move(file_path, INSERTED_DIR / file_path.name)
                print(f"Moved file to '{INSERTED_DIR.name}'.")
                success_count += 1

            except Exception as e:
                print(f"[ERROR] Failed to process {file_path.name}: {e}")
                fail_count += 1
                continue # Move to the next file
    finally:
        if conn:
            conn.close()
            print("\nSnowflake connection closed.")

    print("\n--- Process Summary ---")
    print(f"Successfully processed: {success_count} file(s)")
    print(f"Failed to process: {fail_count} file(s)")
    print("-----------------------")

if __name__ == "__main__":
    main()
