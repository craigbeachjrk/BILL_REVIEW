import os
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from datetime import datetime

import tkinter as tk
from tkinter import filedialog

# Create a Tkinter root window
root = tk.Tk()
root.withdraw()  # Hide the root window

# Ask the user to select a folder
folder_path = filedialog.askdirectory(
    title="Select the folder containing your CSV files"
)

# Ask the user to select the header file
header_file = filedialog.askopenfilename(
    title="Select the CSV file to use for headers"
)

# Read the headers from the specified file
header_df = pd.read_csv(header_file)

# Get the headers (column names) from this file
headers = header_df.columns

# Create an empty list to store DataFrames
dfs = []

# Iterate over all files in the folder
for filename in os.listdir(folder_path):
    if filename.endswith('.csv'):
        # Full path to the current CSV file
        file_path = os.path.join(folder_path, filename)
        
        # Read the CSV file, skipping the header row, and using the headers from the header file
        df = pd.read_csv(file_path, header=None, names=headers, skiprows=1)
        
        # Print the record count for the current file
        print(f"Read {len(df)} records from {filename}")
        
        # Append the DataFrame to the list
        dfs.append(df)

# Concatenate all DataFrames in the list
all_data = pd.concat(dfs, ignore_index=True)
print(f"\nTotal records after combining all files: {len(all_data)}\n")

# --- Generate filename and save CSV ---
try:
    # The original column name from the header file is expected to be 'Reading Date'
    if 'Reading Date' in all_data.columns:
        # Ensure 'Reading Date' column is in datetime format
        date_col = pd.to_datetime(all_data['Reading Date'], errors='coerce')
        
        # Drop rows where date conversion failed, if any
        valid_dates = date_col.dropna()
        
        if not valid_dates.empty:
            min_date = valid_dates.min().strftime('%Y-%m-%d')
            max_date = valid_dates.max().strftime('%Y-%m-%d')
            
            # Create the dynamic filename
            output_filename_csv = f"AllMeterReads - {min_date}-{max_date}.csv"
        else:
            print("WARNING: Could not determine date range from 'Reading Date' column. Using a generic filename.")
            output_filename_csv = "AllMeterReads_output.csv"
    else:
        raise KeyError
except KeyError:
    print("ERROR: 'Reading Date' column not found. Cannot generate dynamic filename. Using a generic filename.")
    output_filename_csv = "AllMeterReads_output.csv"

# Define the output CSV file path
output_file_csv = os.path.join(folder_path, output_filename_csv)

# Write the combined DataFrame to a new CSV file
all_data.to_csv(output_file_csv, index=False)
print(f"CSV file has been successfully saved as {output_file_csv}.")

# --- Snowflake Integration ---

def connect_to_snowflake():
    """
    Securely connects to Snowflake using a private key from .env file.
    """
    load_dotenv()
    try:
        key_path = os.environ["PRIV_KEY_FILE"]
        # Use .get() to make the password optional
        pwd = os.environ.get("PRIV_KEY_FILE_PWD")

        with open(key_path, "rb") as f:
            private_key_bytes = f.read()
        
        # Only use the password if it is a non-empty string
        password_bytes = None
        if pwd and pwd.strip():
            password_bytes = pwd.encode()

        pk = serialization.load_pem_private_key(
            private_key_bytes,
            password=password_bytes
        )
        private_key = pk.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        print("\nAttempting to connect to Snowflake...")
        # NOTE: The user and role must have INSERT permissions on the target table.
        return snowflake.connector.connect(
            user="JRK_READ_WRITE",
            account="EMC25361.us-east-1",
            private_key=private_key,
            warehouse="COMPUTE_WH",
            database="LAITMAN",
            schema="METERREADS",
            role="LAITMAN_RW",
        )
    except FileNotFoundError:
        print("ERROR: Could not find the private key file. Check the PRIV_KEY_FILE path in your .env file.")
        return None
    except KeyError as e:
        print(f"ERROR: Missing environment variable: {e}. Please check your .env file.")
        return None
    except Exception as e:
        print(f"An error occurred while connecting to Snowflake: {e}")
        return None

def validate_and_insert_data(df, conn):
    """
    Validates, transforms, and inserts the dataframe into the Snowflake ALLMETERREADS table.
    """
    table_name = "ALLMETERREADS"
    df_to_insert = df.copy()

    # Explicitly rename columns to match the Snowflake table before normalization
    df_to_insert.rename(columns={
        'Apt Unit#': 'APT_UNIT',
        'Account #': 'ACCOUNT'
    }, inplace=True)

    df_to_insert.columns = [str(col).upper().strip().replace(' ', '_') for col in df_to_insert.columns]

    sql_columns = [
        'PROPERTY_NAME', 'BUILDING_NAME', 'APT_UNIT', 'ACCOUNT', 'MTU_ID', 'PORT',
        'UTILITY', 'READING_DATE', 'IMC', 'COUNT', 'RAW_READ', 'CF', 'FACTORED',
        'RSSI', 'RUNDATETIME'
    ]
    numeric_cols = ['IMC', 'COUNT', 'RAW_READ', 'CF', 'FACTORED', 'RSSI']
    varchar_cols = [col for col in sql_columns if col not in numeric_cols]

    if 'RUNDATETIME' not in df_to_insert.columns:
        df_to_insert['RUNDATETIME'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    missing_cols = [col for col in sql_columns if col not in df_to_insert.columns]
    if missing_cols:
        print(f"ERROR: The following required columns are missing for Snowflake insert: {missing_cols}")
        return

    df_to_insert = df_to_insert[sql_columns]

    # Convert columns to their target types before insertion
    for col in numeric_cols:
        df_to_insert[col] = pd.to_numeric(df_to_insert[col], errors='coerce')
    
    for col in varchar_cols:
        # Convert non-null values to string to prevent type errors on insert
        mask = df_to_insert[col].notna()
        df_to_insert.loc[mask, col] = df_to_insert.loc[mask, col].astype(str)

    # Replace any remaining NaNs with None for Snowflake compatibility
    df_to_insert = df_to_insert.where(pd.notnull(df_to_insert), None)
    
    print(f"Preparing to insert {len(df_to_insert)} rows into Snowflake table {table_name}...")
    try:
        success, nchunks, nrows, _ = write_pandas(
            conn=conn,
            df=df_to_insert,
            table_name=table_name.upper(),
            schema='METERREADS',
            database='LAITMAN'
        )
        if success:
            print(f"Successfully inserted {nrows} rows into {table_name}.")
        else:
            print(f"Failed to insert data into {table_name}.")
    except Exception as e:
        print(f"An error occurred during Snowflake insert: {e}")


# Now, attempt to connect to Snowflake and insert the data
print("\n--- Starting Snowflake Upload ---")
snowflake_conn = connect_to_snowflake()
if snowflake_conn:
    try:
        validate_and_insert_data(all_data, snowflake_conn)
    finally:
        print("Closing Snowflake connection.")
        snowflake_conn.close()
else:
    print("Could not establish Snowflake connection. Skipping data insert.")

