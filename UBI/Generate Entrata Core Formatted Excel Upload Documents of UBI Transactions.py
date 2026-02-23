import tkinter as tk
from tkinter import filedialog
import pandas as pd
import os
from datetime import datetime

# Function to open a file dialog and select a CSV file
def select_csv_file():
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    root.call('wm', 'attributes', '.', '-topmost', True)  # Ensure the dialog is on top
    file_path = filedialog.askopenfilename(
        title="Select a CSV file",
        filetypes=[("CSV files", "*.csv")]  # Allow selecting CSV files
    )
    return file_path

# Function to get folder suffix from user
def get_folder_suffix():
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    root.call('wm', 'attributes', '.', '-topmost', True)  # Ensure the dialog is on top
    from tkinter import simpledialog
    suffix = simpledialog.askstring("Input", "What suffix do you want to add to the folder name? (leave blank if nothing)", parent=root)
    return suffix if suffix else ""

# Main script to read in the selected CSV file as upload_tracker_df
file_path = select_csv_file()
if file_path:
    try:
        # Get folder suffix from user
        folder_suffix = get_folder_suffix()

        # Read in the selected CSV file, ensuring all data is read as strings
        upload_tracker_df = pd.read_csv(file_path, dtype=str)
        print("CSV file successfully loaded into DataFrame.")
        
        # # Add a leading zero to the 'building ID' column
        # if 'building ID' in upload_tracker_df.columns:
        #     upload_tracker_df['building ID'] = upload_tracker_df['building ID'].apply(
        #         lambda x: '0' + x if not x.startswith('0') else x
        #     )
        #     print("Added leading zero to 'building ID' column.")
        # else:
        #     print("Column 'building ID' not found in the DataFrame.")

        # Get the folder where the selected file is located
        base_folder = os.path.dirname(file_path)
        folder_name = 'Excel Manual Uploads'
        if folder_suffix:
            folder_name = f"{folder_name}_{folder_suffix}"
        excel_manual_uploads_folder = os.path.join(base_folder, folder_name)
        os.makedirs(excel_manual_uploads_folder, exist_ok=True)  # Create the folder if it doesn't exist

        # Group the DataFrame by 'property name' and save each group as its own Excel file
        grouped = upload_tracker_df.groupby('property name')
        for property_name, group_df in grouped:
            # Generate a clean file name for each property
            safe_property_name = "".join(
                char if char.isalnum() or char in " _-" else "_" for char in property_name
            )
            # Get the current datetime
            current_datetime = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
            property_file_name = f"UBI - {safe_property_name} - {current_datetime}.xlsx"
            property_file_path = os.path.join(excel_manual_uploads_folder, property_file_name)

            # Save each group DataFrame to its own Excel file
            group_df.to_excel(property_file_path, index=False)
            print(f"Saved {property_name} data to: {property_file_path}")

    except Exception as e:
        print(f"Error reading the selected CSV file: {e}")
else:
    print("No file was selected.")
