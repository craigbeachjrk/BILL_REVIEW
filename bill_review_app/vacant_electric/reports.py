"""
Output generation: Entrata CSV, PDF property reports, audit workbook.
"""
import io
import numpy as np
import pandas as pd
from typing import Optional

from .config import VEConfig


def generate_entrata_csv(agg_df: pd.DataFrame, post_date: str, post_month: str) -> pd.DataFrame:
    """
    Build the 19-column Entrata upload CSV format from aggregated charges.

    Returns a DataFrame with exact Entrata column structure.
    """
    csv = pd.DataFrame()
    csv['property name'] = agg_df['Property']
    csv['lease id'] = np.nan
    csv['building name'] = agg_df['Bldg ID']
    csv['unit number'] = agg_df['Unit ID']
    csv['space number'] = np.nan
    csv['lease status type'] = agg_df['ResiStatus']
    csv['name first'] = np.nan
    csv['name last'] = np.nan
    csv['lease start date'] = np.nan
    csv['lease end date'] = np.nan
    csv['charge code'] = agg_df['Code']
    csv['transaction amount'] = agg_df['Total']
    csv['transaction unpaid amount'] = np.nan
    csv['posted_date'] = post_date
    csv['post_month'] = post_month
    csv['memo'] = agg_df['memo']
    csv['write off only'] = np.nan
    csv['is other income'] = np.nan
    csv['write off amount'] = np.nan
    return csv


def generate_property_pdf(
    entity_id: str,
    property_name: str,
    charges_df: pd.DataFrame,
    month: int,
    year: int,
    total: float,
) -> bytes:
    """
    Generate a landscape PDF report for a single property.

    Args:
        entity_id: Property code (e.g. '01APX')
        property_name: Full property name
        charges_df: Detail rows for this property (from email_df)
        month: Billing month (1-12)
        year: Billing year
        total: Total billback amount

    Returns:
        PDF file content as bytes.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER
    import calendar

    buf = io.BytesIO()
    page_w, page_h = landscape(letter)
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(letter),
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.4 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'VETitle', parent=styles['Heading1'],
        fontSize=14, spaceAfter=4, textColor=colors.HexColor('#1a237e')
    )
    subtitle_style = ParagraphStyle(
        'VESubtitle', parent=styles['Normal'],
        fontSize=10, spaceAfter=8, textColor=colors.HexColor('#424242')
    )
    cell_style = ParagraphStyle(
        'VECell', parent=styles['Normal'], fontSize=7, leading=9
    )
    cell_right = ParagraphStyle(
        'VECellRight', parent=cell_style, alignment=TA_RIGHT
    )
    header_style = ParagraphStyle(
        'VEHeader', parent=styles['Normal'],
        fontSize=7, leading=9, textColor=colors.white, alignment=TA_CENTER
    )

    month_name = calendar.month_name[month]
    elements = [
        Paragraph(f"{property_name} ({entity_id})", title_style),
        Paragraph(f"Vacant Utility Billback — {month_name} {year}  |  Total: ${total:,.2f}", subtitle_style),
        Spacer(1, 4),
    ]

    # Table headers
    col_headers = [
        'Unit', 'Resident', 'Status', 'Utility', 'Bill Start', 'Bill End',
        'Bill Days', 'Overlap Days', 'Amount', 'Prorated', 'Admin', 'Total'
    ]
    header_row = [Paragraph(f"<b>{h}</b>", header_style) for h in col_headers]

    data = [header_row]
    for _, row in charges_df.iterrows():
        def fmt_date(v):
            try:
                return pd.to_datetime(v).strftime('%m/%d/%y')
            except:
                return str(v) if pd.notna(v) else ''

        def fmt_money(v):
            try:
                return f"${float(v):,.2f}"
            except:
                return ''

        unit_label = str(row.get('Unit ID', ''))
        bldg = row.get('Bldg ID', '')
        if bldg and str(bldg) != '01' and str(bldg) != 'nan':
            unit_label = f"{bldg}-{unit_label}"

        data.append([
            Paragraph(unit_label, cell_style),
            Paragraph(str(row.get('Name', '')), cell_style),
            Paragraph(str(row.get('ResiStatus', '')), cell_style),
            Paragraph(str(row.get('Utility', '')), cell_style),
            Paragraph(fmt_date(row.get('Bill Start')), cell_style),
            Paragraph(fmt_date(row.get('Bill End')), cell_style),
            Paragraph(str(int(row['Bill Days'])) if pd.notna(row.get('Bill Days')) else '', cell_right),
            Paragraph(str(int(row['Overlap Days'])) if pd.notna(row.get('Overlap Days')) else '', cell_right),
            Paragraph(fmt_money(row.get('dramount')), cell_right),
            Paragraph(fmt_money(row.get('Prorated Billback', row.get('Prorated_Billback', 0))), cell_right),
            Paragraph(fmt_money(row.get('Admin Charge', row.get('Admin_Charge', 0))), cell_right),
            Paragraph(fmt_money(row.get('Total')), cell_right),
        ])

    col_widths = [
        0.75 * inch,  # Unit
        1.3 * inch,   # Resident
        0.45 * inch,  # Status
        0.85 * inch,  # Utility
        0.7 * inch,   # Bill Start
        0.7 * inch,   # Bill End
        0.5 * inch,   # Bill Days
        0.55 * inch,  # Overlap Days
        0.7 * inch,   # Amount
        0.7 * inch,   # Prorated
        0.6 * inch,   # Admin
        0.7 * inch,   # Total
    ]

    dark_blue = colors.HexColor('#1a237e')
    light_gray = colors.HexColor('#f5f5f5')

    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), dark_blue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]
    # Alternating row shading
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), light_gray))

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)

    doc.build(elements)
    return buf.getvalue()


def generate_audit_workbook(
    final_df: pd.DataFrame,
    unmatched_df: pd.DataFrame,
    match_summary: pd.DataFrame,
    path: str,
    expense_df: Optional[pd.DataFrame] = None,
) -> None:
    """
    Write comprehensive audit workbook with multiple sheets.
    """
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        if unmatched_df is not None and len(unmatched_df) > 0:
            unmatched_detail = unmatched_df[
                ['entityid', 'description', 'Unit String', 'Bldg ID', 'Unit ID', 'Key', 'dramount', 'Utility']
            ].copy()
            unmatched_detail.to_excel(writer, sheet_name='Unmatched Records', index=False)

        if match_summary is not None and len(match_summary) > 0:
            match_summary.to_excel(writer, sheet_name='Match Summary', index=False)

        if final_df is not None and len(final_df) > 0:
            final_df.to_excel(writer, sheet_name='Final Results', index=False)
