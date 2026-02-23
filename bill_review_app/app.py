import os
import io
import json
import time
import hashlib
import datetime as dt
from collections import defaultdict

import streamlit as st
import boto3
import pandas as pd

BUCKET = os.getenv("BUCKET", "jrk-analytics-billing")
ENRICH_PREFIX = os.getenv("ENRICH_PREFIX", "Bill_Parser_4_Enriched_Outputs/")
OVERRIDE_PREFIX = os.getenv("OVERRIDE_PREFIX", "Bill_Parser_5_Overrides/")
DEFAULT_PROFILE = os.getenv("AWS_PROFILE", "")  # empty = use default credentials chain (for App Runner/ECS/EC2)
DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")
REVIEW_TABLE = os.getenv("REVIEW_TABLE", "jrk-bill-review")
REVIEW_QUEUE_URL = os.getenv("REVIEW_QUEUE_URL", "")  # SQS queue URL for processing step
APP_RUNNER_URL = os.getenv("APP_RUNNER_URL", "").strip()  # Optional: public URL for browser WS hints

@st.cache_data(show_spinner=False, ttl=120)
def _s3_client(profile_name: str, region: str):
    if profile_name:
        session = boto3.Session(profile_name=profile_name, region_name=region)
        return session.client("s3")
    # default provider chain (IAM role on App Runner)
    return boto3.client("s3", region_name=region)

@st.cache_data(show_spinner=False, ttl=120)
def _ddb_client(profile_name: str, region: str):
    if profile_name:
        session = boto3.Session(profile_name=profile_name, region_name=region)
        return session.client("dynamodb")
    return boto3.client("dynamodb", region_name=region)

@st.cache_data(show_spinner=False, ttl=120)
def _sqs_client(profile_name: str, region: str):
    if profile_name:
        session = boto3.Session(profile_name=profile_name, region_name=region)
        return session.client("sqs")
    return boto3.client("sqs", region_name=region)

@st.cache_data(show_spinner=False, ttl=120)
def list_dates(profile: str, region: str):
    s3 = _s3_client(profile, region)
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=ENRICH_PREFIX)
    seen = set()
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Expect keys like .../yyyy=2025/mm=09/dd=19/source=s3/filename.jsonl
            parts = key.split("/")
            try:
                y = next(p for p in parts if p.startswith("yyyy="))[5:]
                m = next(p for p in parts if p.startswith("mm="))[3:]
                d = next(p for p in parts if p.startswith("dd="))[3:]
                seen.add((y, m, d))
            except StopIteration:
                continue
    # Return sorted desc by date
    dates = sorted([{ "label": f"{y}-{m}-{d}", "tuple": (y,m,d) } for (y,m,d) in seen], key=lambda x: x["label"], reverse=True)
    return dates

@st.cache_data(show_spinner=True, ttl=60)
def load_day(profile: str, region: str, y: str, m: str, d: str) -> pd.DataFrame:
    s3 = _s3_client(profile, region)
    prefix = f"{ENRICH_PREFIX}yyyy={y}/mm={m}/dd={d}/".replace("==","=")
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET, Prefix=prefix)
    rows = []
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(".jsonl"):
                continue
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
            for idx, line in enumerate(body.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rec["__s3_key__"] = key
                    rec["__row_idx__"] = idx
                    rec["__id__"] = f"{key}#{idx}"
                    rows.append(rec)
                except Exception:
                    continue
    df = pd.DataFrame(rows)
    return df

def write_overrides(profile: str, region: str, y: str, m: str, d: str, overrides: list[dict]):
    if not overrides:
        return None
    s3 = _s3_client(profile, region)
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_prefix = f"{OVERRIDE_PREFIX}yyyy={y}/mm={m}/dd={d}/"
    out_key = f"{out_prefix}overrides_{ts}.jsonl"
    body = "\n".join(json.dumps(o, ensure_ascii=False) for o in overrides) + "\n"
    s3.put_object(Bucket=BUCKET, Key=out_key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    return out_key

def _get_review_status_bulk(profile: str, region: str, ids: list[str]) -> dict:
    if not ids:
        return {}
    ddb = _ddb_client(profile, region)
    keys = [{"pk": {"S": id_}} for id_ in ids]
    # batch get in chunks of 100
    out = {}
    for i in range(0, len(keys), 100):
        chunk = keys[i:i+100]
        resp = ddb.batch_get_item(RequestItems={REVIEW_TABLE: {"Keys": chunk}})
        items = resp.get("Responses", {}).get(REVIEW_TABLE, [])
        for it in items:
            pk = it.get("pk", {}).get("S")
            status = it.get("status", {}).get("S")
            out[pk] = status
    return out

def _put_review_status(profile: str, region: str, id_: str, status: str, user: str):
    ddb = _ddb_client(profile, region)
    ddb.put_item(
        TableName=REVIEW_TABLE,
        Item={
            "pk": {"S": id_},
            "status": {"S": status},
            "updated_by": {"S": user},
            "updated_utc": {"S": dt.datetime.utcnow().isoformat()}
        }
    )

def _submit_to_queue(profile: str, region: str, records: list[dict]):
    if not REVIEW_QUEUE_URL or not records:
        return 0
    sqs = _sqs_client(profile, region)
    sent = 0
    for r in records:
        body = json.dumps(r)
        sqs.send_message(QueueUrl=REVIEW_QUEUE_URL, MessageBody=body)
        sent += 1
    return sent

# --------------- UI ----------------
# Streamlit runtime/network hints for reverse proxies (App Runner)
try:
    # Bind server and relax proxy protections (fronted by App Runner)
    st.set_option("server.address", "0.0.0.0")
    st.set_option("server.port", 8080)
    st.set_option("server.enableCORS", False)
    st.set_option("server.enableXsrfProtection", False)
    # Allow all origins (behind managed proxy) to avoid 403 on WS origin checks
    try:
        st.set_option("server.allowedOrigins", ["*"])
    except Exception:
        pass
    # Some corporate networks/proxies break WS compression
    st.set_option("server.enableWebsocketCompression", False)
    # Help client compute WS URL via explicit browser host/port if provided
    if APP_RUNNER_URL:
        st.set_option("browser.serverAddress", APP_RUNNER_URL)
        st.set_option("browser.serverPort", 443)
except Exception:
    pass

st.set_page_config(page_title="Bill Enrichment Review", layout="wide")

st.sidebar.header("Connection")
profile = st.sidebar.text_input("AWS profile (leave blank on AWS)", value=DEFAULT_PROFILE)
region = st.sidebar.text_input("AWS region", value=DEFAULT_REGION)

st.title("Bill Enrichment Review")

with st.spinner("Scanning dates..."):
    dates = list_dates(profile, region)

if not dates:
    st.warning("No enriched outputs found in S3.")
    st.stop()

selected = st.selectbox("Select processing day (UTC)", options=dates, format_func=lambda d: d["label"])  # type: ignore
(y, m, d) = selected["tuple"]  # type: ignore

if st.button("Load day", type="primary"):
    st.session_state["loaded_day"] = (y, m, d)

loaded = st.session_state.get("loaded_day")
if loaded:
    y, m, d = loaded
    with st.spinner(f"Loading yyyy={y} mm={m} dd={d} ..."):
        df = load_day(profile, region, y, m, d)
    if df.empty:
        st.info("No records for selected day.")
        st.stop()

    # Choose columns to display/edit
    view_cols = [
        "Bill To Name First Line","Vendor Name","Invoice Number","Account Number","Line Item Account Number",
        "Service Address","Service City","Service State","Utility Type","Line Item Description",
        "Bill Period Start","Bill Period End","EnrichedGLAccountNumber","EnrichedGLAccountName","GL_LINE_DESC",
        "GL DESC_NEW","ENRICHED CONSUMPTION","ENRICHED UOM","PDF_LINK"
    ]
    # Ensure present
    cols_present = [c for c in view_cols if c in df.columns]

    # Load review statuses from DDB
    statuses = _get_review_status_bulk(profile, region, df["__id__"].tolist()) if "__id__" in df.columns else {}
    df["__status__"] = df["__id__"].map(lambda k: statuses.get(k, "Pending"))

    st.subheader("Review and override")
    colL, colR = st.columns([2, 1], gap="large")

    with colL:
        edited = st.data_editor(
            df[["__status__"] + cols_present],
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "PDF_LINK": st.column_config.LinkColumn("PDF_LINK"),
                "__status__": st.column_config.SelectboxColumn("Status", options=["Pending","Reviewed","Submitted"], required=True)
            },
            key="editgrid"
        )

    with colR:
        st.write("Preview")
        # allow selecting a row to preview
        options = [f"{i}: {r.get('Invoice Number','')} | {r.get('Service Address','')}" for i, r in df.iterrows()]
        sel = st.selectbox("Select row", options=list(range(len(options))), format_func=lambda i: options[i] if options else "") if options else None
        if sel is not None and 0 <= sel < len(df):
            row = df.iloc[sel]
            st.json({k: row.get(k) for k in view_cols if k in row})
            link = row.get("PDF_LINK")
            if link:
                st.components.v1.iframe(link, height=500)

    st.caption("Tip: Edit GL account fields or descriptions as needed, then click Save overrides.")

    if st.button("Save overrides", type="primary"):
        # Compare edited vs original to emit overrides
        overrides = []
        edited_df = edited
        # Join back to original by row index
        for idx, row in edited_df.iterrows():
            original = df.iloc[idx]
            changed = {}
            for c in cols_present:
                newv = row.get(c)
                oldv = original.get(c)
                if (pd.isna(newv) and pd.isna(oldv)):
                    continue
                if str(newv) != str(oldv):
                    changed[c] = newv
            if changed:
                overrides.append({
                    "source_s3_key": original.get("__s3_key__"),
                    "id": original.get("__id__"),
                    "row_index": int(original.get("__row_idx__", -1)),
                    "invoice_number": original.get("Invoice Number"),
                    "account_number": original.get("Account Number"),
                    "line_item_account_number": original.get("Line Item Account Number"),
                    "override_user": os.getenv("USERNAME") or os.getenv("USER") or "reviewer",
                    "override_utc": dt.datetime.utcnow().isoformat(),
                    "changes": changed
                })
        if not overrides:
            st.success("No changes detected.")
        else:
            out_key = write_overrides(profile, region, y, m, d, overrides)
            if out_key:
                st.success(f"Saved overrides to s3://{BUCKET}/{out_key}")
            else:
                st.error("Failed to write overrides.")

    # Persist statuses to DDB
    if st.button("Save statuses"):
        edited_df = edited
        count = 0
        for idx, row in edited_df.iterrows():
            id_ = df.iloc[idx].get("__id__")
            status = str(row.get("__status__"))
            if id_ and status:
                _put_review_status(profile, region, id_, status, os.getenv("USERNAME") or os.getenv("USER") or "reviewer")
                count += 1
        st.success(f"Saved status for {count} rows")

    # Submit reviewed rows -> SQS + mark Submitted
    if st.button("Submit reviewed"):
        edited_df = edited
        to_submit = []
        for idx, row in edited_df.iterrows():
            if str(row.get("__status__")) != "Reviewed":
                continue
            base = df.iloc[idx]
            to_submit.append({
                "id": base.get("__id__"),
                "s3_key": base.get("__s3_key__"),
                "row_index": int(base.get("__row_idx__", -1)),
                "invoice_number": base.get("Invoice Number"),
                "account_number": base.get("Account Number"),
                "line_item_account_number": base.get("Line Item Account Number"),
                "submitted_by": os.getenv("USERNAME") or os.getenv("USER") or "reviewer",
                "submitted_utc": dt.datetime.utcnow().isoformat(),
            })
        sent = _submit_to_queue(profile, region, to_submit)
        for r in to_submit:
            _put_review_status(profile, region, r["id"], "Submitted", r["submitted_by"])
        st.success(f"Submitted {sent} rows to processing")
else:
    st.info("Select a day and click Load day.")
