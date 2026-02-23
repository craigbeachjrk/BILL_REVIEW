import os, json, boto3, urllib.parse, datetime, re

s3 = boto3.client("s3")

BUCKET = os.environ.get("BUCKET", "jrk-analytics-billing")
PENDING_PREFIX = os.environ.get("PENDING_PREFIX", "Bill_Parser_1_Pending_Parsing/")
REWORK_PREFIX = os.environ.get("REWORK_PREFIX", "Bill_Parser_Rework_Input/")
MAX_META = 1900  # safe under 2KB header


def _get_sidecar(bucket: str, key: str) -> dict:
    side = key.rsplit(".", 1)[0] + ".rework.json"
    try:
        body = s3.get_object(Bucket=bucket, Key=side)["Body"].read()
        return json.loads(body)
    except Exception:
        return {}


def handler(event, ctx):
    out = []
    for rec in event.get("Records", []):
        b = rec["s3"]["bucket"]["name"]
        k = urllib.parse.unquote_plus(rec["s3"]["object"]["key"])
        if not k.lower().endswith(".pdf"):
            continue
        meta = _get_sidecar(b, k)
        notes = str(meta.get("notes", ""))
        # Extract optional structured hints from notes (e.g., "6 lines", "six lines" not supported)
        expected_line_count = None
        m = re.search(r"(\d+)\s*(line|lines|items)", notes.lower())
        if m:
            try:
                expected_line_count = int(m.group(1))
            except Exception:
                expected_line_count = None
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        base = k.split("/")[-1]
        dest_key = f"{PENDING_PREFIX}{ts}_REWORK_{base}"
        # Copy with metadata (notes truncated if necessary)
        md = {"x-amz-meta-rework": "true"}
        if notes:
            md["x-amz-meta-rework-notes"] = notes[:MAX_META]
        if expected_line_count is not None:
            for k in ("x-amz-meta-expected-lines", "x-amz-meta-expected_line_count", "x-amz-meta-line_count", "x-amz-meta-min_lines"):
                md[k] = str(expected_line_count)
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": b, "Key": k},
            Key=dest_key,
            Metadata=md,
            MetadataDirective="REPLACE",
        )
        # Write adjacent sidecars for parsers that read sidecars (.notes.json and .rework.json)
        payload = {
            "notes": notes,
            "instructions": notes,  # duplicate under a second key for compatibility
            "expected_line_count": expected_line_count,
            "expectedLines": expected_line_count,
            "expected_lines": expected_line_count,
            "line_count": expected_line_count,
            "min_lines": expected_line_count,
            "source": {"bucket": b, "key": k},
            "generated_utc": ts,
        }
        base_out = dest_key.rsplit(".", 1)[0]
        for suf in (".notes.json", ".rework.json"):
            s3.put_object(
                Bucket=BUCKET,
                Key=base_out + suf,
                Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json",
            )
        out.append({"forwarded_to": dest_key})
    return {"ok": True, "items": out}
