import os
import re
from datetime import datetime, date
import math
import pandas as pd
import requests
import json
import time
import base64
import logging
from tkinter import Tk, filedialog

from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

from exchangelib import Account, OAuth2Credentials, Configuration, IMPERSONATION, Identity, Message, Mailbox, FileAttachment
from msal import ConfidentialClientApplication
from oauthlib.oauth2 import OAuth2Token

# ------------------------------------------------------------------------------
# Function to open a file dialog for file selection (Invoice Excel file)
def select_invoice_file(title):
    root = Tk()
    root.withdraw()  # Hide the main Tkinter window
    file_path = filedialog.askopenfilename(title=title, filetypes=[("Excel files", "*.xlsx;*.xls")])
    root.destroy()
    if not file_path:
        raise Exception("No file selected for " + title)
    return file_path

# Function to select an output folder
def select_output_folder(title):
    root = Tk()
    root.withdraw()
    folder_path = filedialog.askdirectory(title=title)
    root.destroy()
    if not folder_path:
        raise Exception("No folder selected for " + title)
    return folder_path

# ------------------------------------------------------------------------------
# Global list to store details of bad lines for troubleshooting
bad_lines_log = []

def log_bad_line(bad_line, line_num):
    """
    Callback function for pandas to log bad lines encountered during CSV parsing.
    The bad_line parameter is a list of strings (the fields of that row).
    """
    bad_lines_log.append({"line_number": line_num, "bad_line": ','.join(bad_line)})
    return None

# ------------------------------------------------------------------------------
# Utility function to sanitize filenames (remove/replace forbidden characters)
def sanitize_filename(value):
    return re.sub(r'[\\/*?:"<>|]', "_", str(value))

# ------------------------------------------------------------------------------
# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Azure AD and Exchange settings
CLIENT_ID = 'ee34f50b-08e0-472b-905b-de6bc0282f6b'
TENANT_ID = '4ef87a9f-783f-4d83-8f85-859338ad4346'
CLIENT_SECRET = 'YOUR_CLIENT_SECRET_HERE'
EWS_URL = 'https://outlook.office365.com/EWS/Exchange.asmx'

username = 'jrk_utility_billing_api_3422@jrkpropertyholdingsentratacore'
password = 'YOUR_PASSWORD_HERE'
credentials = f"{username}:{password}"
encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

sendleasedoc_url = 'https://jrkpropertyholdingsentratacore.entrata.com/api/leases'

# ------------------------------------------------------------------------------
# (The JRK reference file remains hard-coded as in the original.)
JRK_REF_FILE = r"H:\Business_Intelligence\6. DE_UTILITIES\Test File Upload\JRK_Ref_File.csv"

# ------------------------------------------------------------------------------
# Get access token using MSAL
def get_access_token():
    app = ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=f'https://login.microsoftonline.com/{TENANT_ID}'
    )
    result = app.acquire_token_for_client(scopes=['https://outlook.office365.com/.default'])
    if 'access_token' in result:
        return result['access_token']
    else:
        raise Exception(f"Failed to acquire token: {result.get('error_description', 'Unknown error')}")

# ------------------------------------------------------------------------------
# Main execution
if __name__ == "__main__":
    try:
        # Let the user select the invoice Excel file
        invoice_file = select_invoice_file("Select Invoice Data Excel File")
        # Let the user select the output folder
        OUTPUT_FOLDER = select_output_folder("Select Output Folder")
        if not os.path.exists(OUTPUT_FOLDER):
            os.makedirs(OUTPUT_FOLDER)
        
        # Read invoice data from the selected Excel file
        df = pd.read_excel(
            invoice_file,
            engine='openpyxl',
            sheet_name=0
        )
        pd.set_option('display.max_rows', None)
        pd.set_option('display.max_columns', None)
        
        # Force date columns to be plain date strings (MM/DD/YYYY)
        date_cols = ["beg_date", "end_date", "due_date", "bill_date"]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%m/%d/%Y')
        
        # Read property reference data from the original JRK reference file
        property_df = pd.read_csv(JRK_REF_FILE, encoding="latin1")
        
        # Group the invoice data by key columns
        group_cols = ["prop_id", "acctno", "beg_date", "end_date"]
        invoice_groups = df.groupby(group_cols, as_index=False)
        
        for _, group_data in invoice_groups:
            property_id   = group_data["prop_id"].iloc[0]
            account_num   = group_data["acctno"].iloc[0]
            prop_name     = group_data["property"].iloc[0]
            resident_name = group_data["resident"].iloc[0]
            bill_start    = group_data["beg_date"].iloc[0]
            bill_end      = group_data["end_date"].iloc[0]
            due_date      = group_data["due_date"].iloc[0]
            bill_date     = group_data["bill_date"].iloc[0]
            p_i           = group_data["p_i"].iloc[0]
            lease_id      = group_data["lease_id"].iloc[0]
        
            lease_id = int(lease_id)
            p_i = int(p_i)
        
            balance_forward = group_data["delinq_bal"].iloc[0]
            balance_forward = 0 if pd.isna(balance_forward) else balance_forward
        
            resi_address = group_data["mailaddr"].iloc[0]
            amount_due   = group_data["total"].iloc[0]
        
            # Retrieve additional property details from the reference CSV
            prop_attn       = property_df.loc[property_df["prop_ID"] == property_id, "prop_attn"].iloc[0]
            prop_add1       = property_df.loc[property_df["prop_ID"] == property_id, "prop_remit_add1"].iloc[0]
            prop_add2       = property_df.loc[property_df["prop_ID"] == property_id, "prop_remit_csz"].iloc[0]
            prop_phone      = property_df.loc[property_df["prop_ID"] == property_id, "prop_phone"].iloc[0]
            prop_delinquent = property_df.loc[property_df["prop_ID"] == property_id, "prop_msg1"].iloc[0]
            prop_not_a_bill = property_df.loc[property_df["prop_ID"] == property_id, "prop_msg2"].iloc[0]
        
            # Utility charges
            gas       = group_data["gas"].iloc[0]
            water     = group_data["water"].iloc[0]
            sewer     = group_data["sewer"].iloc[0]
            acct_fee  = group_data["acct_fee"].iloc[0]
            ubi_deposit = group_data["UBI Dep"].iloc[0]
            late_fee  = group_data["late_fee"].iloc[0]
            service   = group_data['srv_chg'].iloc[0]
            trash     = group_data["trash"].iloc[0]
            elec      = group_data["elec"].iloc[0]
            pestc     = group_data["pest control"].iloc[0]
            kvfd      = group_data["KVFD Fee"].iloc[0]
            storm     = group_data["Storm Water"].iloc[0]
            hoa       = group_data["HOA Fee"].iloc[0]
            env       = group_data["envf"].iloc[0]
            beg_meter = group_data["beg_meter"].iloc[0]
            end_meter = group_data["end_meter"].iloc[0]
        
            service = pd.to_numeric(service, errors='coerce')
        
            # Replace NaNs
            water     = 0 if pd.isna(water) else water
            sewer     = 0 if pd.isna(sewer) else sewer
            gas       = 0 if pd.isna(gas) else gas
            acct_fee  = 0 if pd.isna(acct_fee) else acct_fee
            late_fee  = 0 if pd.isna(late_fee) else late_fee
            service   = 0 if pd.isna(service) else service
            trash     = 0 if pd.isna(trash) else trash
            elec      = 0 if pd.isna(elec) else elec
            pestc     = 0 if pd.isna(pestc) else pestc
            kvfd      = 0 if pd.isna(kvfd) else kvfd
            storm     = 0 if pd.isna(storm) else storm
            env       = 0 if pd.isna(env) else env
            hoa       = 0 if pd.isna(hoa) else hoa
            ubi_deposit = 0 if pd.isna(ubi_deposit) else ubi_deposit
            beg_meter = -1 if pd.isna(beg_meter) else beg_meter
            end_meter = -1 if pd.isna(end_meter) else end_meter
        
            # Build table data for the PDF
            table_data = [
                ["Current Charges", " "],
                ["WATER", f"${water:0.2f}"],
                ["SEWER", f"${sewer:0.2f}"],
                ["GAS", f"${gas:0.2f}"],
                ["ACCOUNT FEE", f"${acct_fee:0.2f}"],
                ["LATE FEE", f"${late_fee:0.2f}"],
                ["SERVICE CHARGE", f"${service:0.2f}"],
                ["TRASH", f"${trash:0.2f}"],
                ["ELECTRIC", f"${elec:0.2f}"],
                ["PEST CONTROL", f"${pestc:0.2f}"],
                ["KVFD FEE", f"${kvfd:0.2f}"],
                ["STORM WATER", f"${storm:0.2f}"],
                ["ENVIRONMENTAL", f"${env:0.2f}"],
                ["HOA FEE", f"${hoa:0.2f}"],
                ["UBI DEPOSIT", f"${ubi_deposit:0.2f}"],
            ]
        
            total_charges = sum([
                water, sewer, gas, acct_fee, late_fee, service,
                trash, elec, pestc, kvfd, storm, env, hoa, ubi_deposit
            ])
        
            # File naming
            safe_account_num = sanitize_filename(account_num)
            safe_bill_start  = sanitize_filename(bill_start)
            # pdf_filename = f"UBI-{safe_account_num}-{safe_bill_start}--UPDATED.pdf"
            pdf_filename = f"UBI-{p_i}-{lease_id}-{safe_account_num}-{safe_bill_start}.pdf"
            pdf_path = os.path.join(OUTPUT_FOLDER, pdf_filename)
        
            # ------------------------------------------------------------------------------
            # REPORTLAB PDF GENERATION
            doc = SimpleDocTemplate(pdf_path, pagesize=LETTER)
            styles = getSampleStyleSheet()
            centered_style = ParagraphStyle(name='Centered', parent=styles['Normal'], alignment=1, fontSize=9)
            elements = []
        
            headings_style = ParagraphStyle(
                name='Headings',
                parent=styles['Normal'],
                fontName="Helvetica-Bold",
                textColor=colors.white,
                alignment=1,
                fontSize=9,
            )
            headings_left = ParagraphStyle(
                name='Headings_lt',
                parent=styles['Normal'],
                fontName="Helvetica-Bold",
                textColor=colors.white,
                alignment=0,
                fontSize=9,
            )
            headings_right = ParagraphStyle(
                name='Headings_rt',
                parent=headings_left,
                alignment=2,
            )
            small_right = ParagraphStyle(
                name='smaller_rt',
                fontName="Helvetica",
                textColor=colors.black,
                alignment=2,
                fontSize=9,
            )
            small_left = ParagraphStyle(
                name='smaller_lt',
                fontName="Helvetica",
                textColor=colors.black,
                alignment=0,
                fontSize=9,
            )
            small_center = ParagraphStyle(
                name='smaller_center',
                parent=small_right,
                alignment=1,
            )
            boldface = ParagraphStyle(
                name='bold_txt',
                parent=styles['Normal'],
                fontName="Helvetica-Bold",
                textColor=colors.black,
                alignment=0,
                fontSize=9,
            )
            boldface_right = ParagraphStyle(
                name='boldface_right',
                parent=boldface,
                alignment=2
            )
        
            # TOP BOXES
            left_box_data = [
                [Paragraph(f"<b>{str(prop_name).upper()}</b>", centered_style)],
                [Paragraph(str(prop_attn), centered_style)],
                [Paragraph(str(prop_add1), centered_style)],
                [Paragraph(str(prop_add2), centered_style)],
                [Paragraph(str(prop_phone), centered_style)],
            ]
            left_box = Table(left_box_data, colWidths=[3 * inch])
            left_box.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
        
            right_box_data = [
                [Paragraph("BILLING DATE", headings_left), Paragraph(str(bill_date), small_right)],
                [Paragraph("ACCOUNT NUMBER", headings_left), Paragraph(str(account_num), small_right)],
                [Paragraph("PROPERTY", headings_left), Paragraph(str(prop_name).upper(), small_right)],
                [Paragraph("", headings_left), Paragraph(str(resi_address).upper(), small_right)],
                [Paragraph("RESIDENT", headings_left), Paragraph(str(resident_name).upper(), small_right)],
                [Paragraph("SERVICE DATES", headings_left), Paragraph(f"{bill_start} - {bill_end}", small_right)],
            ]
            if beg_meter >= 0:
                right_box_data.append([
                    Paragraph("METER READING", headings_left),
                    Paragraph(f"BEGIN: {int(beg_meter)}    END: {int(end_meter)}", small_right)
                ])
            right_box_data.append([
                Paragraph("DUE BY", headings_left),
                Paragraph(str(due_date), small_right)
            ])
        
            right_box = Table(right_box_data, colWidths=[1.5 * inch, 2.25 * inch])
            right_box.setStyle(TableStyle([
                ("SPACEBEFORE", (0, 0), (-1, -1), 0),
                ("SPACEAFTER", (0, 0), (-1, -1), 0),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#404040")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, 1), (0, -1), 0.3, colors.white),
                ("LINEBELOW", (0, 1), (0, -1), 0.3, colors.white),
            ]))
        
            top_boxes = Table([[left_box, right_box]], colWidths=[3 * inch, 3.95 * inch])
            top_boxes.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP")
            ]))
            elements.append(top_boxes)
            elements.append(Spacer(1, 20))
        
            # MIDDLE BOXES
            m1_data = [
                [Paragraph("BALANCE FORWARD", headings_style)],
                [f"${balance_forward:,.2f}"],
            ]
            m1 = Table(m1_data, colWidths=[2.17 * inch, 2.17 * inch])
            m1.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (0, 1), (-1, 1), "CENTER"),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.black),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.black),
            ]))
        
            m2_data = [
                [Paragraph("CURRENT CHARGES", headings_style)],
                [f"${total_charges:,.2f}"],
            ]
            m2 = Table(m2_data, colWidths=[2.17 * inch, 2.17 * inch])
            m2.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (0, 1), (-1, 1), "CENTER"),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.black),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.black),
            ]))
        
            m3_data = [
                [Paragraph(f"AMOUNT DUE BY {due_date}", headings_style)],
                [f"${amount_due:,.2f}"],
            ]
            m3 = Table(m3_data, colWidths=[2.17 * inch, 2.17 * inch])
            m3.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (0, 1), (-1, 1), "CENTER"),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.black),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.black),
            ]))
        
            middle_boxes = Table([[m1, m2, m3]], colWidths=[2.27 * inch, 2.27 * inch, 2.27 * inch])
            middle_boxes.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(middle_boxes)
            elements.append(Spacer(1, 12))
        
            # BOTTOM BOXES
            b1_data = [
                [Paragraph("IMPORTANT MESSAGE", headings_left)],
                [Paragraph(str(prop_delinquent), small_center)],
                [Paragraph(f"<i>{str(prop_not_a_bill)}</i>", small_center)],
            ]
            b1 = Table(b1_data, colWidths=[3.6 * inch, 3.6 * inch], rowHeights=[None, 120, 120])
            b1.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 1), (-1, 1), "BOTTOM"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.black),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.black),
                ("ROWHEIGHT", (0, 0), (-1, -1), 100),
            ]))
        
            utilities_table = Table(table_data, hAlign="CENTER")
            utilities_table.setStyle(TableStyle([
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ]))
        
            b2_data = [
                [Paragraph("UTILITY", headings_left), Paragraph("CHARGES", headings_right)],
                [Paragraph("BALANCE FORWARD", boldface), Paragraph(f"${balance_forward:,.2f}", boldface_right)],
                [Paragraph(""), Paragraph("")],
                [Paragraph("<u><b>CURRENT CHARGES</b></u>", boldface), Paragraph("")],
            ]
            for row in table_data[1:]:
                util_name, amount = row
                numeric_amount = float(amount.replace("$", "")) if isinstance(amount, str) else amount
                if numeric_amount and not math.isnan(numeric_amount):
                    b2_data.append([Paragraph(util_name, small_left), Paragraph(amount, small_right)])
            b2_data.append([Paragraph(""), Paragraph("")])
            b2_data.append([Paragraph("TOTAL CURRENT CHARGES", boldface), Paragraph(f"${total_charges:0.2f}", boldface_right)])
            b2_data.append([Paragraph(""), Paragraph("")])
            b2_data.append([Paragraph("<u><b>ACCOUNT BALANCE</b></u>", boldface), Paragraph("")])
            b2_data.append([Paragraph(""), Paragraph("")])
            b2_data.append([
                Paragraph(f"TOTAL AMOUNT DUE BY {due_date}", boldface),
                Paragraph(f"${amount_due:0.2f}", boldface_right)
            ])
        
            b2 = Table(b2_data, colWidths=[1.9 * inch, 1.1 * inch])
            b2.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#404040")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (0, 1), (-1, 1), "LEFT"),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.black),
                ("BOX", (0, 0), (-1, -1), 0.3, colors.black),
            ]))
        
            bottom_boxes = Table([[b1, b2]], colWidths=[3.7 * inch, 3.1 * inch])
            bottom_boxes.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            elements.append(bottom_boxes)
            elements.append(Spacer(1, 20))
        
            # Build and save PDF
            doc.build(elements)
            print(f"Created PDF: {pdf_path}")
        
        # ------------------------------------------------------------------------------
        # Save bad lines if any
        if bad_lines_log:
            bad_log_path = os.path.join(OUTPUT_FOLDER, "bad_lines_log.csv")
            pd.DataFrame(bad_lines_log).to_csv(bad_log_path, index=False)
            logger.info(f"Bad lines log written to: {bad_log_path}")
        else:
            logger.info("No bad lines encountered during CSV parsing.")
    
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
