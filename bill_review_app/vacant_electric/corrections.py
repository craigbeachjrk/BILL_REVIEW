"""
AI correction loading and application (two stages).

Stage 1: UNIT_STRING corrections - fix the raw parsed unit string before mapping.
Stage 2: MAPPED_UNIT corrections - override the mapped unit ID after property mapping.
"""
import pandas as pd
from typing import Tuple, Dict


def load_corrections(csv_path: str) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str]]:
    """
    Load correction lookup from CSV.

    Returns:
        (unit_string_corrections, mapped_unit_corrections) where:
        - unit_string_corrections: {original_unit_string: corrected_unit_string}
        - mapped_unit_corrections: {(entity_id, original_mapped_unit): corrected_unit_id}
    """
    unit_string_corrections = {}
    mapped_unit_corrections = {}

    try:
        corrections_df = pd.read_csv(csv_path)
        for _, crow in corrections_df.iterrows():
            if (crow['CORRECTION_TYPE'] == 'UNIT_STRING'
                    and pd.notna(crow.get('ORIGINAL_UNIT_STRING'))
                    and pd.notna(crow.get('AI_CORRECTED_UNIT_STRING'))):
                unit_string_corrections[crow['ORIGINAL_UNIT_STRING']] = crow['AI_CORRECTED_UNIT_STRING']
            elif (crow['CORRECTION_TYPE'] == 'MAPPED_UNIT'
                  and pd.notna(crow.get('ENTITY_ID'))
                  and pd.notna(crow.get('ORIGINAL_MAPPED_UNIT'))
                  and pd.notna(crow.get('AI_UNIT_ID'))):
                mapped_unit_corrections[(crow['ENTITY_ID'], str(crow['ORIGINAL_MAPPED_UNIT']))] = str(crow['AI_UNIT_ID'])
    except Exception as e:
        print(f"  Warning: Could not load AI corrections: {e}")

    return unit_string_corrections, mapped_unit_corrections


def apply_unit_string_corrections(df: pd.DataFrame, corrections: Dict[str, str]) -> Tuple[pd.DataFrame, int]:
    """
    Stage 1: Apply unit string corrections before property mapping.
    Replaces empty-after-@ unit strings with corrected values.

    Returns:
        (modified_df, count_of_corrections_applied)
    """
    count = 0
    for idx in range(len(df)):
        us = df.at[df.index[idx], 'Unit String']
        if us and us in corrections:
            df.at[df.index[idx], 'Unit String'] = corrections[us]
            count += 1
    return df, count


def apply_mapped_unit_corrections(df: pd.DataFrame, corrections: Dict[Tuple[str, str], str]) -> Tuple[pd.DataFrame, int]:
    """
    Stage 2: Apply mapped unit corrections after property mapping.
    Overrides unit IDs where the MAP_* function produced wrong results.

    Returns:
        (modified_df, count_of_corrections_applied)
    """
    count = 0
    for idx in range(len(df)):
        eid = df.at[df.index[idx], 'entityid']
        uid = df.at[df.index[idx], 'Unit ID']
        if uid and (eid, str(uid)) in corrections:
            df.at[df.index[idx], 'Unit ID'] = corrections[(eid, str(uid))]
            count += 1
    return df, count
