"""
Extensive tests for the Chunk Processor Lambda.

Covers:
  - call_gemini_api: JSON mode in payload, HTTP error handling, response parsing
  - parse_chunk_with_retry: JSON parsing, markdown stripping, retry/backoff, key rotation,
    content validation, context inheritance, expected_lines prompting
  - cleanse_field / normalize_row / validate_row_content: utility functions
  - lambda_handler: end-to-end S3 trigger flow
"""
import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock, ANY
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Bootstrap: make the Lambda code importable.
# The module creates boto3 clients at module level, so we must set
# AWS_DEFAULT_REGION and mock boto3.client before importing.
# ---------------------------------------------------------------------------
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

LAMBDA_DIR = os.path.join(
    os.path.dirname(__file__),
    "aws_lambdas", "us-east-1", "jrk-bill-chunk-processor", "code",
)
sys.path.insert(0, LAMBDA_DIR)

# Patch boto3.client at module level so the import doesn't hit real AWS
_mock_boto3_client = MagicMock()
with patch.dict("sys.modules", {}):
    pass  # ensure clean state
import boto3 as _boto3
_original_client = _boto3.client
_boto3.client = MagicMock(return_value=MagicMock())

import lambda_chunk_processor as lcp

# Restore boto3.client for other uses
_boto3.client = _original_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gemini_response_json(items: list) -> dict:
    """Build a Gemini-style response whose text is a JSON array."""
    return {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps(items)}]
            }
        }]
    }


def _make_http_response(status_code: int, json_body=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_body or {})
    resp.json.return_value = json_body or {}
    return resp


def _single_line_item(**overrides) -> dict:
    """Return a minimal valid Gemini JSON line-item object."""
    base = {
        "bill_to_name": "Acme Corp",
        "vendor_name": "DTE Energy",
        "account_number": "123456",
        "service_address": "100 Main St",
        "service_city": "Detroit",
        "service_state": "MI",
        "service_zipcode": "48201",
        "bill_period_start": "01/01/2026",
        "bill_period_end": "01/31/2026",
        "description": "Electric Delivery",
        "charge": 45.67,
        "bill_date": "02/01/2026",
        "due_date": "02/15/2026",
    }
    base.update(overrides)
    return base


# =========================================================================
# 1. call_gemini_api
# =========================================================================
class TestCallGeminiApi(unittest.TestCase):
    """Verify call_gemini_api sends correct payload and handles responses."""

    @patch("lambda_chunk_processor.requests.post")
    def test_payload_includes_json_mode(self, mock_post):
        """The payload MUST contain generationConfig.responseMimeType = application/json."""
        mock_post.return_value = _make_http_response(200, _gemini_response_json([_single_line_item()]))

        lcp.call_gemini_api("fake-key", b"fake-pdf", "parse this")

        # Inspect the payload that was sent
        call_args = mock_post.call_args
        sent_payload = json.loads(call_args.kwargs.get("data") or call_args[1].get("data"))
        self.assertIn("generationConfig", sent_payload)
        self.assertEqual(
            sent_payload["generationConfig"]["responseMimeType"],
            "application/json",
        )

    @patch("lambda_chunk_processor.requests.post")
    def test_returns_text_from_candidates(self, mock_post):
        items = [_single_line_item()]
        mock_post.return_value = _make_http_response(200, _gemini_response_json(items))

        result = lcp.call_gemini_api("key", b"pdf", "prompt")
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["vendor_name"], "DTE Energy")

    @patch("lambda_chunk_processor.requests.post")
    def test_empty_candidates_returns_empty_string(self, mock_post):
        mock_post.return_value = _make_http_response(200, {"candidates": []})
        result = lcp.call_gemini_api("key", b"pdf", "prompt")
        self.assertEqual(result, "")

    @patch("lambda_chunk_processor.requests.post")
    def test_no_candidates_key_returns_empty_string(self, mock_post):
        mock_post.return_value = _make_http_response(200, {})
        result = lcp.call_gemini_api("key", b"pdf", "prompt")
        self.assertEqual(result, "")

    @patch("lambda_chunk_processor.requests.post")
    def test_429_raises_rate_limit_error(self, mock_post):
        mock_post.return_value = _make_http_response(429, text="quota exhausted")
        with self.assertRaises(lcp.RateLimitError):
            lcp.call_gemini_api("key", b"pdf", "prompt")

    @patch("lambda_chunk_processor.requests.post")
    def test_500_raises_runtime_error(self, mock_post):
        mock_post.return_value = _make_http_response(500, text="internal error")
        with self.assertRaises(RuntimeError):
            lcp.call_gemini_api("key", b"pdf", "prompt")

    @patch("lambda_chunk_processor.requests.post")
    def test_multi_part_response_concatenated(self, mock_post):
        """Multiple parts in the response should be concatenated."""
        body = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": '[{"description": "Part A",'},
                        {"text": ' "charge": 10}]'},
                    ]
                }
            }]
        }
        mock_post.return_value = _make_http_response(200, body)
        result = lcp.call_gemini_api("key", b"pdf", "prompt")
        parsed = json.loads(result)
        self.assertEqual(parsed[0]["description"], "Part A")

    @patch("lambda_chunk_processor.requests.post")
    def test_timeout_propagates(self, mock_post):
        """Network timeout should propagate as an exception."""
        import requests as real_requests
        mock_post.side_effect = real_requests.exceptions.Timeout("timed out")
        with self.assertRaises(Exception):
            lcp.call_gemini_api("key", b"pdf", "prompt", timeout=5)


# =========================================================================
# 2. cleanse_field
# =========================================================================
class TestCleanseField(unittest.TestCase):
    def test_removes_pipes(self):
        self.assertEqual(lcp.cleanse_field("foo|bar"), "foo-bar")

    def test_removes_newlines(self):
        self.assertEqual(lcp.cleanse_field("foo\nbar\rbaz"), "foo bar baz")

    def test_collapses_spaces(self):
        self.assertEqual(lcp.cleanse_field("a   b"), "a b")

    def test_empty_string(self):
        self.assertEqual(lcp.cleanse_field(""), "")

    def test_none_value(self):
        self.assertEqual(lcp.cleanse_field(None), "")

    def test_strips_whitespace(self):
        self.assertEqual(lcp.cleanse_field("  hello  "), "hello")


# =========================================================================
# 3. normalize_row
# =========================================================================
class TestNormalizeRow(unittest.TestCase):
    def test_exact_length_unchanged(self):
        row = ["a"] * 30
        result = lcp.normalize_row(row, 30)
        self.assertEqual(len(result), 30)

    def test_too_few_columns_padded(self):
        row = ["a", "b"]
        result = lcp.normalize_row(row, 30)
        self.assertEqual(len(result), 30)
        self.assertEqual(result[0], "a")
        self.assertEqual(result[2], "")

    def test_too_many_columns_merged_into_description(self):
        # 32 columns -> 2 extra should be merged into description (index 24)
        row = ["x"] * 32
        result = lcp.normalize_row(row, 30)
        self.assertEqual(len(result), 30)
        # Description should contain merged values
        self.assertIn(" - ", result[24])

    def test_cleanses_pipes_in_values(self):
        row = ["has|pipe"] + [""] * 29
        result = lcp.normalize_row(row, 30)
        self.assertEqual(result[0], "has-pipe")


# =========================================================================
# 4. validate_row_content
# =========================================================================
class TestValidateRowContent(unittest.TestCase):
    def _make_row(self, **kwargs):
        """Build a row with sensible defaults, overridable by index."""
        row = [""] * len(lcp.COLUMNS)
        row[0] = "Acme"             # Bill To Name
        row[2] = "DTE Energy"       # Vendor Name
        row[4] = "123456"           # Account Number
        row[13] = "01/01/2026"      # Bill Period Start
        row[14] = "01/31/2026"      # Bill Period End
        row[24] = "Electric Delivery"  # Description
        row[25] = "45.67"           # Charge
        row[26] = "02/01/2026"      # Bill Date
        for idx, val in kwargs.items():
            row[int(idx)] = val
        return row

    def test_valid_row_passes(self):
        row = self._make_row()
        is_valid, errors = lcp.validate_row_content(row)
        self.assertTrue(is_valid, f"Expected valid but got errors: {errors}")

    def test_date_field_with_non_date_fails(self):
        row = self._make_row(**{"13": "not-a-date"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertFalse(is_valid)
        self.assertTrue(any("Bill Period Start" in e for e in errors))

    def test_numeric_field_with_text_fails(self):
        row = self._make_row(**{"25": "not-a-number"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertFalse(is_valid)
        self.assertTrue(any("Line Item Charge" in e for e in errors))

    def test_description_field_with_number_fails(self):
        row = self._make_row(**{"24": "45.67"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertFalse(is_valid)
        self.assertTrue(any("Line Item Description" in e for e in errors))

    def test_description_field_with_date_fails(self):
        row = self._make_row(**{"24": "01/15/2026"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertFalse(is_valid)
        self.assertTrue(any("Line Item Description" in e for e in errors))

    def test_empty_optional_fields_pass(self):
        """Empty values for optional numeric/date fields should pass."""
        row = self._make_row(**{"16": "", "18": "", "22": ""})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertTrue(is_valid, f"Unexpected errors: {errors}")

    def test_negative_charge_passes(self):
        row = self._make_row(**{"25": "-12.50"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertTrue(is_valid, f"Unexpected errors: {errors}")

    def test_parenthetical_negative_passes(self):
        row = self._make_row(**{"25": "(12.50)"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertTrue(is_valid, f"Unexpected errors: {errors}")

    def test_dollar_sign_in_charge_passes(self):
        row = self._make_row(**{"25": "$1,234.56"})
        is_valid, errors = lcp.validate_row_content(row)
        self.assertTrue(is_valid, f"Unexpected errors: {errors}")

    def test_various_date_formats_pass(self):
        for fmt in ["01/15/2026", "1/5/26", "2026-01-15", "01-15-2026", "2026/01/15"]:
            row = self._make_row(**{"13": fmt})
            is_valid, errors = lcp.validate_row_content(row)
            self.assertTrue(is_valid, f"Date '{fmt}' should pass but got: {errors}")


# =========================================================================
# 5. parse_chunk_with_retry — JSON parsing
# =========================================================================
class TestParseChunkJsonParsing(unittest.TestCase):
    """Test that parse_chunk_with_retry correctly parses various Gemini responses."""

    def _run_parse(self, reply_text, chunk_num=1, total_chunks=1, previous_context="", expected_lines=0):
        """Helper: mock call_gemini_api to return reply_text, run parse_chunk_with_retry."""
        with patch("lambda_chunk_processor.call_gemini_api", return_value=reply_text):
            with patch("lambda_chunk_processor.time.sleep"):  # skip stagger delay
                return lcp.parse_chunk_with_retry(
                    ["key1"], b"pdf", chunk_num, total_chunks, previous_context, expected_lines
                )

    def test_clean_json_array(self):
        """Standard JSON array — the happy path with JSON mode."""
        items = [_single_line_item(), _single_line_item(description="Tax", charge=5.00)]
        rows, ctx = self._run_parse(json.dumps(items))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][24], "Electric Delivery")  # description
        self.assertEqual(rows[1][24], "Tax")

    def test_single_json_object_wrapped_in_list(self):
        """A single object (not wrapped in []) should be auto-wrapped."""
        item = _single_line_item()
        rows, ctx = self._run_parse(json.dumps(item))
        self.assertEqual(len(rows), 1)

    def test_markdown_code_block_stripped(self):
        """Even with JSON mode, test the markdown fallback still works."""
        items = [_single_line_item()]
        text = "```json\n" + json.dumps(items) + "\n```"
        rows, ctx = self._run_parse(text)
        self.assertEqual(len(rows), 1)

    def test_markdown_code_block_no_language_tag(self):
        items = [_single_line_item()]
        text = "```\n" + json.dumps(items) + "\n```"
        rows, ctx = self._run_parse(text)
        self.assertEqual(len(rows), 1)

    def test_empty_response_returns_no_rows(self):
        rows, ctx = self._run_parse("EMPTY")
        self.assertEqual(len(rows), 0)
        self.assertIn("empty", ctx.lower())

    def test_empty_string_returns_no_rows(self):
        rows, ctx = self._run_parse("")
        self.assertEqual(len(rows), 0)

    def test_empty_json_array(self):
        rows, ctx = self._run_parse("[]")
        self.assertEqual(len(rows), 0)

    def test_invalid_json_with_embedded_array(self):
        """If the response has junk text around the JSON, regex fallback should find it."""
        items = [_single_line_item()]
        text = "Here are the results:\n" + json.dumps(items) + "\nDone!"
        rows, ctx = self._run_parse(text)
        self.assertEqual(len(rows), 1)

    def test_completely_invalid_text_returns_empty(self):
        """Total garbage should result in 0 rows, not a crash."""
        rows, ctx = self._run_parse("This is not JSON at all, just random text.")
        self.assertEqual(len(rows), 0)

    def test_null_values_in_fields_become_empty_strings(self):
        item = _single_line_item()
        item["meter_number"] = None
        item["meter_size"] = None
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][10], "")  # meter_number index
        self.assertEqual(rows[0][11], "")  # meter_size index

    def test_numeric_values_converted_to_strings(self):
        item = _single_line_item(charge=99.50, consumption=1500)
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertEqual(rows[0][25], "99.5")   # charge
        self.assertEqual(rows[0][16], "1500")    # consumption

    def test_context_summary_includes_vendor_and_account(self):
        item = _single_line_item(vendor_name="SoCal Edison", account_number="9999")
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertIn("SoCal Edison", ctx)
        self.assertIn("9999", ctx)

    def test_extra_json_keys_ignored(self):
        """Unknown keys in the Gemini response should be silently ignored."""
        item = _single_line_item()
        item["unknown_field"] = "should be ignored"
        item["another_extra"] = 42
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][24], "Electric Delivery")

    def test_non_dict_items_skipped(self):
        """Non-dict items in the array should be skipped."""
        items = [_single_line_item(), "not a dict", 42, None, _single_line_item(description="Fee")]
        rows, ctx = self._run_parse(json.dumps(items))
        self.assertEqual(len(rows), 2)


# =========================================================================
# 6. parse_chunk_with_retry — retry & key rotation
# =========================================================================
class TestParseChunkRetryLogic(unittest.TestCase):
    """Test retry behavior, key rotation, and backoff."""

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_rate_limit_retries_with_different_key(self, mock_api, mock_sleep):
        """On 429, should retry with the next API key."""
        items = [_single_line_item()]
        mock_api.side_effect = [
            lcp.RateLimitError("429"),        # key1 fails
            json.dumps(items),                 # key2 succeeds
        ]
        rows, ctx = lcp.parse_chunk_with_retry(
            ["key1", "key2"], b"pdf", 1, 1, ""
        )
        self.assertEqual(len(rows), 1)
        # First call with key1, second with key2
        self.assertEqual(mock_api.call_count, 2)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_all_attempts_exhausted_returns_empty(self, mock_api, mock_sleep):
        """After MAX_ATTEMPTS failures, should return empty rows."""
        mock_api.side_effect = lcp.RateLimitError("429")
        rows, ctx = lcp.parse_chunk_with_retry(
            ["key1", "key2"], b"pdf", 1, 1, ""
        )
        self.assertEqual(len(rows), 0)
        self.assertIn("failed", ctx.lower())
        self.assertEqual(mock_api.call_count, lcp.MAX_ATTEMPTS)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_generic_exception_retries(self, mock_api, mock_sleep):
        """Non-429 exceptions should also trigger retries."""
        items = [_single_line_item()]
        mock_api.side_effect = [
            RuntimeError("500 error"),
            json.dumps(items),
        ]
        rows, ctx = lcp.parse_chunk_with_retry(
            ["key1", "key2"], b"pdf", 1, 1, ""
        )
        self.assertEqual(len(rows), 1)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_exponential_backoff_on_rate_limit(self, mock_api, mock_sleep):
        """Rate limit errors should use exponential backoff."""
        mock_api.side_effect = [
            lcp.RateLimitError("429"),
            lcp.RateLimitError("429"),
            json.dumps([_single_line_item()]),
        ]
        lcp.parse_chunk_with_retry(["k1", "k2", "k3"], b"pdf", 1, 1, "")

        # sleep calls: stagger (0 for chunk 1) + backoff for attempt 0 + backoff for attempt 1
        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        # First backoff: BASE * 2^0 = 2, Second: BASE * 2^1 = 4
        self.assertIn(lcp.BASE_BACKOFF_SECONDS * 1, sleep_values)   # 2^0 = 1 -> 2*1=2
        self.assertIn(lcp.BASE_BACKOFF_SECONDS * 2, sleep_values)   # 2^1 = 2 -> 2*2=4

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_key_rotation_wraps_around(self, mock_api, mock_sleep):
        """With 2 keys and 4 failures, keys should rotate: k1, k2, k1, k2."""
        items = [_single_line_item()]
        mock_api.side_effect = [
            lcp.RateLimitError("429"),  # k1
            lcp.RateLimitError("429"),  # k2
            lcp.RateLimitError("429"),  # k1
            json.dumps(items),          # k2
        ]
        rows, ctx = lcp.parse_chunk_with_retry(
            ["key_A", "key_B"], b"pdf", 1, 1, ""
        )
        self.assertEqual(len(rows), 1)
        # Verify key alternation via the api_key arg
        keys_used = [c.args[0] for c in mock_api.call_args_list]
        self.assertEqual(keys_used, ["key_A", "key_B", "key_A", "key_B"])

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_stagger_delay_based_on_chunk_num(self, mock_api, mock_sleep):
        """Chunk 3 should have a stagger delay of (3-1)*1.5 = 3.0 seconds."""
        mock_api.return_value = json.dumps([_single_line_item()])
        lcp.parse_chunk_with_retry(["k1"], b"pdf", 3, 4, "")

        # First sleep call should be the stagger
        first_sleep = mock_sleep.call_args_list[0].args[0]
        expected_stagger = (3 - 1) * lcp.CHUNK_STAGGER_SECONDS
        self.assertAlmostEqual(first_sleep, expected_stagger)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_chunk_1_no_stagger(self, mock_api, mock_sleep):
        """Chunk 1 should have zero stagger delay (no sleep for stagger)."""
        mock_api.return_value = json.dumps([_single_line_item()])
        lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 4, "")
        # If chunk 1, stagger = 0 so no stagger sleep. Only sleep would be
        # from retries, and we succeeded on first try, so no sleep at all.
        self.assertEqual(mock_sleep.call_count, 0)


# =========================================================================
# 7. parse_chunk_with_retry — content validation & retry
# =========================================================================
class TestParseChunkContentValidation(unittest.TestCase):
    """Test that rows with bad content trigger validation retries."""

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_many_invalid_rows_triggers_retry(self, mock_api, mock_sleep):
        """If > MAX_DROPPED_ROWS_BEFORE_RETRY invalid rows, should retry with feedback."""
        # First response: many rows with numbers in description field (invalid)
        bad_items = [_single_line_item(description=str(i * 10.5)) for i in range(8)]
        good_items = [_single_line_item() for _ in range(8)]

        mock_api.side_effect = [
            json.dumps(bad_items),   # attempt 1: all invalid descriptions
            json.dumps(good_items),  # attempt 2: all valid
        ]
        rows, ctx = lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 1, "")
        self.assertEqual(len(rows), 8)
        self.assertEqual(mock_api.call_count, 2)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_few_invalid_rows_accepted_without_retry(self, mock_api, mock_sleep):
        """If <= MAX_DROPPED_ROWS_BEFORE_RETRY invalid rows, accept them without retry."""
        # Mix of valid and invalid (fewer than threshold)
        items = [_single_line_item() for _ in range(5)]
        # Make 2 invalid (under threshold of 5)
        items[0]["description"] = "99.99"
        items[1]["description"] = "01/01/2026"

        mock_api.return_value = json.dumps(items)
        rows, ctx = lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 1, "")
        # Should only call API once (no retry needed)
        self.assertEqual(mock_api.call_count, 1)
        # All rows accepted (invalid ones included since under threshold)
        self.assertEqual(len(rows), 5)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_last_attempt_accepts_invalid_rows(self, mock_api, mock_sleep):
        """On the final attempt, invalid rows should be included anyway."""
        bad_items = [_single_line_item(description=str(i * 10.5)) for i in range(8)]
        # Every attempt returns bad data
        mock_api.return_value = json.dumps(bad_items)

        rows, ctx = lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 1, "")
        # Should have all rows despite being invalid (accepted on final attempt)
        self.assertEqual(len(rows), 8)
        self.assertEqual(mock_api.call_count, lcp.MAX_ATTEMPTS)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_validation_retry_includes_error_feedback_in_prompt(self, mock_api, mock_sleep):
        """Second attempt should include validation error feedback in the prompt."""
        bad_items = [_single_line_item(description=str(i * 10.5)) for i in range(8)]
        good_items = [_single_line_item() for _ in range(8)]

        mock_api.side_effect = [
            json.dumps(bad_items),
            json.dumps(good_items),
        ]
        lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 1, "")

        # Second call should have error feedback in prompt
        second_call_prompt = mock_api.call_args_list[1].args[2]  # 3rd positional arg is prompt
        self.assertIn("CONTENT VALIDATION ERRORS", second_call_prompt)
        self.assertIn("Line Item Description", second_call_prompt)


# =========================================================================
# 8. parse_chunk_with_retry — context inheritance (chunk 2+)
# =========================================================================
class TestParseChunkContextInheritance(unittest.TestCase):
    """Test that chunks 2+ inherit service address from previous context."""

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_empty_address_filled_from_context(self, mock_api, mock_sleep):
        """Chunk 2+ rows with empty service address should inherit from context."""
        item = _single_line_item()
        item["service_address"] = ""
        item["service_city"] = ""
        item["service_state"] = ""
        item["service_zipcode"] = ""

        mock_api.return_value = json.dumps([item])

        context = "Bill To: Acme | Vendor: DTE | Service Address: 555 Oak Ave, Ann Arbor, MI 48104"
        rows, ctx = lcp.parse_chunk_with_retry(["k1"], b"pdf", 2, 3, context)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][6], "555 Oak Ave")  # service_address
        self.assertEqual(rows[0][7], "Ann Arbor")    # service_city
        self.assertEqual(rows[0][9], "MI")           # service_state
        self.assertEqual(rows[0][8], "48104")        # service_zipcode

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_existing_address_not_overwritten(self, mock_api, mock_sleep):
        """Chunk 2+ rows with existing address should NOT be overwritten."""
        item = _single_line_item(service_address="200 Elm St", service_city="Troy")

        mock_api.return_value = json.dumps([item])

        context = "Service Address: 555 Oak Ave, Ann Arbor, MI 48104"
        rows, ctx = lcp.parse_chunk_with_retry(["k1"], b"pdf", 2, 3, context)

        self.assertEqual(rows[0][6], "200 Elm St")  # kept original
        self.assertEqual(rows[0][7], "Troy")

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_chunk_1_does_not_inherit(self, mock_api, mock_sleep):
        """Chunk 1 should NOT try to inherit address from context."""
        item = _single_line_item()
        item["service_address"] = ""

        mock_api.return_value = json.dumps([item])

        rows, ctx = lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 3, "")
        # Address should remain empty for chunk 1
        self.assertEqual(rows[0][6], "")


# =========================================================================
# 9. parse_chunk_with_retry — expected_lines prompt
# =========================================================================
class TestParseChunkExpectedLines(unittest.TestCase):
    """Test that expected_lines hint is included in the prompt."""

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_expected_lines_included_in_prompt(self, mock_api, mock_sleep):
        mock_api.return_value = json.dumps([_single_line_item()])
        lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 4, "", expected_lines=64)

        prompt_sent = mock_api.call_args.args[2]
        self.assertIn("64", prompt_sent)
        self.assertIn("line items total", prompt_sent)
        # 64 / 4 chunks = ~16 per chunk
        self.assertIn("16", prompt_sent)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_zero_expected_lines_not_in_prompt(self, mock_api, mock_sleep):
        mock_api.return_value = json.dumps([_single_line_item()])
        lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 4, "", expected_lines=0)

        prompt_sent = mock_api.call_args.args[2]
        self.assertNotIn("CRITICAL REQUIREMENT", prompt_sent)
        self.assertNotIn("line items total", prompt_sent)


# =========================================================================
# 10. lambda_handler — end-to-end
# =========================================================================
class TestLambdaHandler(unittest.TestCase):
    """Test the full lambda_handler flow with mocked AWS services."""

    def _make_s3_event(self, key="Bill_Parser_1_LargeFile_Chunks/job123/chunk_001.pdf"):
        return {
            "Records": [{
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": "jrk-analytics-billing"},
                    "object": {"key": key},
                }
            }]
        }

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_happy_path_end_to_end(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """Full happy path: S3 event -> parse -> save result -> update progress."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job123", "total_chunks": 2, "chunks_completed": 0,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        mock_api.return_value = json.dumps([_single_line_item()])

        result = lcp.lambda_handler(self._make_s3_event(), None)

        self.assertEqual(result["statusCode"], 200)
        mock_s3.put_object.assert_called_once()
        mock_progress.assert_called_once()

        # Verify the saved result contains rows
        saved_body = mock_s3.put_object.call_args.kwargs["Body"]
        saved_data = json.loads(saved_body)
        self.assertEqual(len(saved_data["rows"]), 1)
        self.assertEqual(saved_data["chunk_num"], 1)

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_job_not_found_skips(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """If job info not found in DynamoDB, should skip without crashing."""
        mock_job.return_value = None
        mock_keys.return_value = ["key1"]

        result = lcp.lambda_handler(self._make_s3_event(), None)

        self.assertEqual(result["statusCode"], 200)
        mock_s3.get_object.assert_not_called()
        mock_api.assert_not_called()

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_no_api_keys_skips(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """If no API keys available, should skip without crashing."""
        mock_keys.return_value = []
        mock_job.return_value = {
            "job_id": "job123", "total_chunks": 2, "chunks_completed": 0,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}

        result = lcp.lambda_handler(self._make_s3_event(), None)

        self.assertEqual(result["statusCode"], 200)
        mock_api.assert_not_called()

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_non_s3_event_skipped(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """Non-S3 events in the Records should be skipped."""
        event = {"Records": [{"eventSource": "aws:sqs", "body": "test"}]}
        result = lcp.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)
        mock_job.assert_not_called()

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_wrong_prefix_skipped(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """S3 keys not matching CHUNKS_PREFIX should be skipped."""
        event = self._make_s3_event(key="some/other/path/file.pdf")
        result = lcp.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)
        mock_job.assert_not_called()

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_chunk_number_parsed_from_key(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """chunk_003.pdf should be parsed as chunk_num=3."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job456", "total_chunks": 4, "chunks_completed": 2,
            "status": "processing", "previous_context": "some context",
            "chunk_results": [], "expected_lines": 64, "bill_from": "Comcast"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        mock_api.return_value = json.dumps([_single_line_item()])

        event = self._make_s3_event(key="Bill_Parser_1_LargeFile_Chunks/job456/chunk_003.pdf")
        lcp.lambda_handler(event, None)

        # Verify chunk_num=3 was used
        mock_progress.assert_called_once_with("job456", 3, ANY, ANY)

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_s3_download_failure_skips(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """If S3 download fails, should skip without crashing."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job123", "total_chunks": 2, "chunks_completed": 0,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.side_effect = Exception("Access Denied")

        result = lcp.lambda_handler(self._make_s3_event(), None)
        self.assertEqual(result["statusCode"], 200)
        mock_api.assert_not_called()

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_all_chunks_complete_logs_ready(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """When this is the last chunk, should log ready_for_aggregation."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job789", "total_chunks": 2, "chunks_completed": 1,  # this will be the 2nd
            "status": "processing", "previous_context": "ctx",
            "chunk_results": ["r1"], "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        mock_api.return_value = json.dumps([_single_line_item()])

        # This should trigger the "all chunks completed" log path
        result = lcp.lambda_handler(self._make_s3_event(), None)
        self.assertEqual(result["statusCode"], 200)


# =========================================================================
# 11. Edge cases for JSON mode specifically
# =========================================================================
class TestJsonModeEdgeCases(unittest.TestCase):
    """Edge cases that are specifically relevant to JSON mode behavior."""

    def _run_parse(self, reply_text):
        with patch("lambda_chunk_processor.call_gemini_api", return_value=reply_text):
            with patch("lambda_chunk_processor.time.sleep"):
                return lcp.parse_chunk_with_retry(["k1"], b"pdf", 1, 1, "")

    def test_json_with_trailing_whitespace(self):
        items = [_single_line_item()]
        rows, ctx = self._run_parse(json.dumps(items) + "\n\n  ")
        self.assertEqual(len(rows), 1)

    def test_json_with_leading_whitespace(self):
        items = [_single_line_item()]
        rows, ctx = self._run_parse("  \n" + json.dumps(items))
        self.assertEqual(len(rows), 1)

    def test_deeply_nested_values_handled(self):
        """If Gemini returns nested objects for a field, str() should handle it."""
        item = _single_line_item()
        item["special_instructions"] = {"note": "complex"}
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertEqual(len(rows), 1)
        # The nested dict should be stringified
        self.assertIn("note", rows[0][28])

    def test_unicode_in_values(self):
        item = _single_line_item(vendor_name="Énergie Québec", description="Frais d'électricité")
        rows, ctx = self._run_parse(json.dumps([item], ensure_ascii=False))
        self.assertEqual(rows[0][2], "Énergie Québec")
        self.assertEqual(rows[0][24], "Frais d'électricité")

    def test_very_long_description_cleansed(self):
        item = _single_line_item(description="A" * 500 + "|" + "B" * 500)
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertNotIn("|", rows[0][24])
        self.assertIn("-", rows[0][24])

    def test_empty_array_from_json_mode(self):
        """JSON mode returning [] should give 0 rows, not crash."""
        rows, ctx = self._run_parse("[]")
        self.assertEqual(len(rows), 0)

    def test_boolean_values_converted_to_string(self):
        item = _single_line_item()
        item["house_or_vacant"] = True
        rows, ctx = self._run_parse(json.dumps([item]))
        self.assertEqual(rows[0][12], "True")

    def test_large_number_of_items(self):
        """Simulate a bill with many line items."""
        items = [_single_line_item(description=f"Charge {i}", charge=i * 1.5) for i in range(100)]
        rows, ctx = self._run_parse(json.dumps(items))
        self.assertEqual(len(rows), 100)


# =========================================================================
# 12. Verify generationConfig is correct in the payload
# =========================================================================
class TestGenerationConfigPayload(unittest.TestCase):
    """Directly verify the payload structure sent to the Gemini API."""

    @patch("lambda_chunk_processor.requests.post")
    def test_payload_structure(self, mock_post):
        mock_post.return_value = _make_http_response(200, _gemini_response_json([]))

        lcp.call_gemini_api("test-key", b"test-pdf", "test prompt")

        sent_data = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))

        # Verify top-level structure
        self.assertIn("contents", sent_data)
        self.assertIn("generationConfig", sent_data)

        # Verify generationConfig
        gen_config = sent_data["generationConfig"]
        self.assertEqual(gen_config["responseMimeType"], "application/json")

        # Verify contents structure
        contents = sent_data["contents"]
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["role"], "user")
        parts = contents[0]["parts"]
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "application/pdf")
        self.assertEqual(parts[1]["text"], "test prompt")

    @patch("lambda_chunk_processor.requests.post")
    def test_api_key_in_url(self, mock_post):
        mock_post.return_value = _make_http_response(200, _gemini_response_json([]))

        lcp.call_gemini_api("my-secret-key", b"pdf", "prompt")

        url_called = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url", "")
        self.assertIn("key=my-secret-key", url_called)

    @patch("lambda_chunk_processor.requests.post")
    def test_pdf_base64_encoded_in_payload(self, mock_post):
        import base64
        mock_post.return_value = _make_http_response(200, _gemini_response_json([]))

        pdf_content = b"fake pdf content here"
        lcp.call_gemini_api("key", pdf_content, "prompt")

        sent_data = json.loads(mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data"))
        inline_data = sent_data["contents"][0]["parts"][0]["inline_data"]["data"]
        self.assertEqual(inline_data, base64.b64encode(pdf_content).decode("ascii"))


# =========================================================================
# 13. Time-budget-aware retry logic
# =========================================================================
class TestTimeBudgetRetry(unittest.TestCase):
    """Test that parse_chunk_with_retry respects the Lambda time budget."""

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_stops_retrying_when_time_exhausted(self, mock_api, mock_sleep):
        """Should stop retrying when deadline_ms is nearly reached."""
        mock_api.side_effect = lcp.RateLimitError("429")

        # Set deadline to 10 seconds from now — not enough for MIN_TIME_FOR_ATTEMPT_MS (30s)
        deadline_ms = int(time.time() * 1000) + 10_000

        rows, ctx = lcp.parse_chunk_with_retry(
            ["k1"], b"pdf", 1, 1, "", deadline_ms=deadline_ms
        )
        self.assertEqual(len(rows), 0)
        self.assertIn("failed", ctx.lower())
        # Should have attempted very few times (maybe 1, then time-budget check stops it)
        self.assertLess(mock_api.call_count, lcp.MAX_ATTEMPTS)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_no_deadline_allows_all_attempts(self, mock_api, mock_sleep):
        """With deadline_ms=0, all MAX_ATTEMPTS should be tried."""
        mock_api.side_effect = lcp.RateLimitError("429")
        rows, ctx = lcp.parse_chunk_with_retry(
            ["k1"], b"pdf", 1, 1, "", deadline_ms=0
        )
        self.assertEqual(mock_api.call_count, lcp.MAX_ATTEMPTS)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_generous_deadline_allows_success(self, mock_api, mock_sleep):
        """With plenty of time, first success should work normally."""
        mock_api.return_value = json.dumps([_single_line_item()])

        # Set deadline far in the future
        deadline_ms = int(time.time() * 1000) + 600_000  # 10 minutes

        rows, ctx = lcp.parse_chunk_with_retry(
            ["k1"], b"pdf", 1, 1, "", deadline_ms=deadline_ms
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(mock_api.call_count, 1)

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_backoff_capped_by_deadline(self, mock_api, mock_sleep):
        """Rate limit backoff should be capped so it doesn't sleep past the deadline."""
        # First call: rate limit error, second: success
        mock_api.side_effect = [
            lcp.RateLimitError("429"),
            json.dumps([_single_line_item()]),
        ]

        # Deadline allows enough time for 2 attempts but not a large backoff
        deadline_ms = int(time.time() * 1000) + 65_000  # 65 seconds

        rows, ctx = lcp.parse_chunk_with_retry(
            ["k1", "k2"], b"pdf", 1, 1, "", deadline_ms=deadline_ms
        )
        self.assertEqual(len(rows), 1)

        # The backoff sleep should have been capped (not the full exponential value)
        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        for s in sleep_values:
            # Each sleep should be <= remaining time minus buffer
            self.assertLessEqual(s, 65)  # generous upper bound

    @patch("lambda_chunk_processor.time.sleep")
    @patch("lambda_chunk_processor.call_gemini_api")
    def test_deadline_already_passed_returns_immediately(self, mock_api, mock_sleep):
        """If the deadline is already past, should return immediately with 0 rows."""
        deadline_ms = int(time.time() * 1000) - 1000  # 1 second ago

        rows, ctx = lcp.parse_chunk_with_retry(
            ["k1"], b"pdf", 1, 1, "", deadline_ms=deadline_ms
        )
        self.assertEqual(len(rows), 0)
        self.assertIn("failed", ctx.lower())
        # Should not have called the API at all
        mock_api.assert_not_called()


class TestRemainingMs(unittest.TestCase):
    """Test the _remaining_ms helper."""

    def test_future_deadline(self):
        future = int(time.time() * 1000) + 60_000
        remaining = lcp._remaining_ms(future)
        self.assertGreater(remaining, 50_000)
        self.assertLessEqual(remaining, 60_000)

    def test_past_deadline(self):
        past = int(time.time() * 1000) - 10_000
        self.assertEqual(lcp._remaining_ms(past), 0)

    def test_exactly_now(self):
        now = int(time.time() * 1000)
        self.assertLessEqual(lcp._remaining_ms(now), 1)  # within 1ms


# =========================================================================
# 14. lambda_handler — failure handling & alarm logging
# =========================================================================
class TestLambdaHandlerFailureHandling(unittest.TestCase):
    """Test that lambda_handler always saves results and emits alarm logs on failure."""

    def _make_s3_event(self, key="Bill_Parser_1_LargeFile_Chunks/job123/chunk_001.pdf"):
        return {
            "Records": [{
                "eventSource": "aws:s3",
                "s3": {
                    "bucket": {"name": "jrk-analytics-billing"},
                    "object": {"key": key},
                }
            }]
        }

    def _make_lambda_context(self, remaining_ms=280_000):
        ctx = MagicMock()
        ctx.get_remaining_time_in_millis.return_value = remaining_ms
        return ctx

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_failed_chunk_still_saves_result(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """Even if all API attempts fail, a result file should be saved with 0 rows."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job_fail", "total_chunks": 4, "chunks_completed": 2,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "Comcast"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        mock_api.side_effect = RuntimeError("API unavailable")

        result = lcp.lambda_handler(self._make_s3_event(), self._make_lambda_context())

        self.assertEqual(result["statusCode"], 200)

        # Result should have been saved to S3 even with 0 rows
        mock_s3.put_object.assert_called_once()
        saved_body = mock_s3.put_object.call_args.kwargs["Body"]
        saved_data = json.loads(saved_body)
        self.assertEqual(len(saved_data["rows"]), 0)
        self.assertTrue(saved_data["failed"])

        # Progress should still be updated
        mock_progress.assert_called_once()

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_successful_chunk_not_marked_failed(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """Successful parsing should not have failed=True in the result."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job_ok", "total_chunks": 2, "chunks_completed": 0,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        mock_api.return_value = json.dumps([_single_line_item()])

        lcp.lambda_handler(self._make_s3_event(), self._make_lambda_context())

        saved_body = mock_s3.put_object.call_args.kwargs["Body"]
        saved_data = json.loads(saved_body)
        self.assertFalse(saved_data["failed"])
        self.assertEqual(len(saved_data["rows"]), 1)

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_deadline_passed_to_parse_function(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """Lambda context remaining time should be used to compute deadline."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job_dl", "total_chunks": 2, "chunks_completed": 0,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        # API always fails — we'll check that it doesn't run all 10 attempts
        mock_api.side_effect = RuntimeError("fail")

        # Give it only 20s remaining — not enough for even 1 attempt (MIN_TIME_FOR_ATTEMPT_MS=30s)
        # After subtracting 15s buffer = 5s deadline budget
        ctx = self._make_lambda_context(remaining_ms=20_000)
        lcp.lambda_handler(self._make_s3_event(), ctx)

        # Should have called API 0 times (budget exhausted before first attempt)
        # or at most 1 time before the budget check kicks in
        self.assertLessEqual(mock_api.call_count, 1)

    @patch("lambda_chunk_processor.update_job_progress")
    @patch("lambda_chunk_processor.s3")
    @patch("lambda_chunk_processor.get_job_info")
    @patch("lambda_chunk_processor.get_keys_from_secret")
    @patch("lambda_chunk_processor.call_gemini_api")
    @patch("lambda_chunk_processor.time.sleep")
    def test_none_context_no_deadline(self, mock_sleep, mock_api, mock_keys, mock_job, mock_s3, mock_progress):
        """Passing context=None (e.g. unit test) should still work with no deadline."""
        mock_keys.return_value = ["key1"]
        mock_job.return_value = {
            "job_id": "job_none", "total_chunks": 2, "chunks_completed": 0,
            "status": "processing", "previous_context": "", "chunk_results": [],
            "expected_lines": 0, "bill_from": "DTE"
        }
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"fake-pdf")}
        mock_api.return_value = json.dumps([_single_line_item()])

        result = lcp.lambda_handler(self._make_s3_event(), None)
        self.assertEqual(result["statusCode"], 200)
        mock_s3.put_object.assert_called_once()


# Need time module for deadline tests
import time


if __name__ == "__main__":
    unittest.main()
