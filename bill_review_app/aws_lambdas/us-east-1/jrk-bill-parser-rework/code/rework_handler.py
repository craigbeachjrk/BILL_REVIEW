import os, json, boto3, urllib.parse, datetime

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
        bill_from = str(meta.get("Bill From") or meta.get("bill_from") or "").strip()
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        base = k.split("/")[-1]
        dest_key = f"{PENDING_PREFIX}{ts}_REWORK_{base}"
        # Copy with metadata (notes truncated if necessary)
        md = {"x-amz-meta-rework": "true"}
        if notes:
            md["x-amz-meta-rework-notes"] = notes[:MAX_META]
        if bill_from:
            md["x-amz-meta-bill-from"] = bill_from[:MAX_META]
        s3.copy_object(
            Bucket=BUCKET,
            CopySource={"Bucket": b, "Key": k},
            Key=dest_key,
            Metadata=md,
            MetadataDirective="REPLACE",
        )
        # Write adjacent .notes.json for parsers that read sidecars
        side_out = dest_key.rsplit(".", 1)[0] + ".notes.json"
        s3.put_object(
            Bucket=BUCKET,
            Key=side_out,
            Body=json.dumps({
                "notes": notes,
                "source": {"bucket": b, "key": k},
                "generated_utc": ts,
                "Bill From": bill_from,
            }, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        out.append({"forwarded_to": dest_key})
    return {"ok": True, "items": out}
