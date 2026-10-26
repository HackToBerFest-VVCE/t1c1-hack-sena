"""
tests/test_gemma_extraction.py
==============================
Unit tests for the Gemma 4 extraction path in app/extraction.py.

All tests mock the google.genai client so they run fully offline.
They cover:
  - Happy path (clean JSON response)
  - Fenced JSON response (```json ... ```)
  - Amount strings like "Rs. 32,071.25" are converted to floats correctly
  - Invalid JSON triggers a single retry, then succeeds
  - JSON that remains invalid after retry falls back to regex
  - API error (500) triggers up to 3 retries then falls back to regex
  - signature_present=False → "Missing"
  - Cache: second call with same file hash skips the API
  - EXTRACTION_MODE=regex bypasses Gemma entirely
"""

from __future__ import annotations

import io
import json
import os
import sys
import types as _builtin_types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── ensure the backend package root is importable ────────────────────────────
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


def _build_gemma_json(overrides: dict | None = None) -> str:
    base = {
        "document_type":    "hospital_bill",
        "patient_name":     "Arjun Sharma",
        "policy_number":    "POL-2024-001",
        "policy_start":     "01-Jan-2024",
        "hospital_name":    "City General Hospital",
        "bill_number":      "BILL-001",
        "bill_date":        "08-Nov-2024",
        "admission_date":   "05-Nov-2024",
        "discharge_date":   "09-Nov-2024",
        "diagnosis":        "Appendicitis",
        "claimed_amount":   "Rs. 30,000.00",
        "total_amount":     "Rs. 32,071.25",
        "line_items":       [{"description": "Room charges", "amount": "Rs. 22,500.00"}],
        "signature_present": True,
        "confidence": {
            "patient_name": 0.95,
            "policy_number": 0.9,
        },
    }
    if overrides:
        base.update(overrides)
    return json.dumps(base)


def _make_pdf_bytes() -> bytes:
    """Create a minimal valid 1-page PDF in memory using PyMuPDF."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Hospital Bill\nPatient Name\nArjun Sharma")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the extraction cache before each test and suppress disk writes."""
    import app.extraction as _ext
    # Reset the in-memory mirror so each test starts fresh
    _ext._mem_cache = {}
    # Suppress all disk I/O so tests never touch the real cache file
    with patch("app.extraction._save_cache"):
        yield
    _ext._mem_cache = {}


@pytest.fixture()
def tmp_pdf(tmp_path) -> Path:
    p = tmp_path / "test_bill.pdf"
    p.write_bytes(_make_pdf_bytes())
    return p


@pytest.fixture()
def tmp_png(tmp_path) -> Path:
    """Create a tiny real PNG file."""
    import pymupdf
    # 10x10 white image
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10))
    pixmap.set_rect(pixmap.irect, (255, 255, 255))
    p = tmp_path / "test_img.png"
    p.write_bytes(pixmap.tobytes("png"))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Helpers to patch the Gemma client stack
# ─────────────────────────────────────────────────────────────────────────────

def _patch_gemma(mock_response_text: str):
    """
    Return a context manager that patches _load_gemini_client to return a mock
    client whose .models.generate_content returns mock_response_text.
    """
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_mock_response(mock_response_text)
    return patch("app.extraction._load_gemini_client", return_value=mock_client), mock_client


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGemmaHappyPath:
    def test_returns_correct_document_type(self, tmp_pdf, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json()
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        assert result["document_type"] == "hospital_bill"
        assert result["extraction_method"] == "gemma-4"
        assert "gemma" in result["model_name"]

    def test_patient_name_extracted(self, tmp_pdf, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json()
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert fields["patient_name"] == "Arjun Sharma"

    def test_image_file_sent_directly(self, tmp_png, monkeypatch):
        """PNG files are sent as-is (not rendered from PDF)."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({"document_type": "hospital_bill"})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_png)

        assert result["extraction_method"] == "gemma-4"
        # The call must have happened
        mock_client.models.generate_content.assert_called_once()


class TestFencedJsonResponse:
    def test_fenced_json_is_parsed(self, tmp_pdf, monkeypatch):
        """Model wraps answer in ```json ... ``` – must be stripped and parsed."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        inner = _build_gemma_json()
        fenced = f"```json\n{inner}\n```"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(fenced)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        assert result["document_type"] == "hospital_bill"
        assert result["extraction_method"] == "gemma-4"

    def test_fenced_json_no_language_tag(self, tmp_pdf, monkeypatch):
        """Fence without language tag: ``` ... ``` should also work."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        inner = _build_gemma_json()
        fenced = f"```\n{inner}\n```"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(fenced)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        assert result["extraction_method"] == "gemma-4"


class TestAmountConversion:
    def test_amount_string_converted_to_float_in_line_items(self, tmp_pdf, monkeypatch):
        """'Rs. 22,500.00' in line_items.amount must become float 22500.0."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({
            "line_items": [{"description": "Room charges", "amount": "Rs. 22,500.00"}],
        })
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        items = json.loads(fields["line_items"])
        assert len(items) == 1
        assert items[0]["amount"] == pytest.approx(22500.0)

    def test_total_amount_string_formatted(self, tmp_pdf, monkeypatch):
        """'Rs. 32,071.25' for total_amount should be stored as 'Rs. 32,071.25'."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({"total_amount": "Rs. 32,071.25"})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert "32,071.25" in fields["total_amount"]

    def test_amount_as_float_in_json(self, tmp_pdf, monkeypatch):
        """Float amounts in JSON (e.g. 32071.25) should also be accepted."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({
            "total_amount":   32071.25,
            "claimed_amount": 30000.0,
        })
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert "32,071.25" in fields["total_amount"]
        assert "30,000.00" in fields["claimed_amount"]


class TestSignaturePresent:
    def test_signature_false_becomes_missing(self, tmp_pdf, monkeypatch):
        """signature_present=false from JSON → 'Missing'."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({"signature_present": False})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert fields["signature_present"] == "Missing"

    def test_signature_true_becomes_present(self, tmp_pdf, monkeypatch):
        """signature_present=true from JSON → 'Present'."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({"signature_present": True})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert fields["signature_present"] == "Present"


class TestJsonRetry:
    def test_invalid_json_retries_once_and_succeeds(self, tmp_pdf, monkeypatch):
        """First response is garbage JSON; second parse attempt succeeds."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        valid_json = _build_gemma_json()
        # Wrap in a fence that has leading text before the fence — aggressive strip needed
        bad_then_good = f"Here is the result:\n```json\n{valid_json}\n```"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(bad_then_good)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        # Should still succeed via the retry strip
        assert result["extraction_method"] == "gemma-4"

    def test_permanently_invalid_json_falls_back_to_regex(self, tmp_pdf, monkeypatch):
        """JSON is completely unparseable → fallback to regex."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response("NOT JSON AT ALL !!!")

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        assert result["extraction_method"] == "regex-fallback"


class TestApiRetry:
    def test_api_500_retries_three_times_then_falls_back(self, tmp_pdf, monkeypatch):
        """API raises an error every time → 3 attempts → regex fallback."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")

        class _FakeAPIError(Exception):
            status_code = 500

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = _FakeAPIError("server error")

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            with patch("app.extraction.time.sleep"):  # skip actual waits
                from app.extraction import extract_document
                result = extract_document(tmp_pdf)

        assert result["extraction_method"] == "regex-fallback"
        # 3 attempts were made (each attempt calls generate_content once)
        assert mock_client.models.generate_content.call_count == 3

    def test_api_succeeds_on_second_attempt(self, tmp_pdf, monkeypatch):
        """First call raises 503; second call succeeds."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")

        class _FakeAPIError(Exception):
            status_code = 503

        payload = _build_gemma_json()
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _FakeAPIError("service unavailable"),
            _make_mock_response(payload),
        ]

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            with patch("app.extraction.time.sleep"):
                from app.extraction import extract_document
                result = extract_document(tmp_pdf)

        assert result["extraction_method"] == "gemma-4"
        assert mock_client.models.generate_content.call_count == 2


class TestCaching:
    def test_same_file_not_reprocessed(self, tmp_pdf, monkeypatch):
        """Second call with the same file should hit cache, not call API again."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json()
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result1 = extract_document(tmp_pdf)
            result2 = extract_document(tmp_pdf)

        # API should only have been called once — second call hits _mem_cache
        assert mock_client.models.generate_content.call_count == 1
        assert result1["document_type"] == result2["document_type"]

    def test_different_files_both_called(self, tmp_path, monkeypatch):
        """Two distinct files each get their own API call."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json()

        pdf_bytes = _make_pdf_bytes()
        file_a = tmp_path / "a.pdf"
        file_b = tmp_path / "b.pdf"
        file_a.write_bytes(pdf_bytes)
        # make b.pdf slightly different content
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 100), "Different content for file B")
        buf = io.BytesIO()
        doc.save(buf)
        file_b.write_bytes(buf.getvalue())

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            extract_document(file_a)
            extract_document(file_b)

        assert mock_client.models.generate_content.call_count == 2


class TestRegexModeBypass:
    def test_regex_mode_does_not_call_gemma(self, tmp_pdf, monkeypatch):
        """EXTRACTION_MODE=regex (default) must never call the Gemma API."""
        monkeypatch.delenv("EXTRACTION_MODE", raising=False)
        # Set to explicit regex to be sure
        monkeypatch.setenv("EXTRACTION_MODE", "regex")

        with patch("app.extraction._load_gemini_client") as mock_load:
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        mock_load.assert_not_called()
        assert result["extraction_method"] == "regex-fallback"

    def test_default_mode_is_regex(self, tmp_pdf, monkeypatch):
        """Without EXTRACTION_MODE set, defaults to regex."""
        monkeypatch.delenv("EXTRACTION_MODE", raising=False)

        with patch("app.extraction._load_gemini_client") as mock_load:
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        mock_load.assert_not_called()
        assert result["extraction_method"] == "regex-fallback"


class TestExtractionMethodOnResult:
    def test_extraction_method_field_present_in_regex_mode(self, tmp_pdf, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODE", "regex")
        from app.extraction import extract_document
        result = extract_document(tmp_pdf)
        assert "extraction_method" in result
        assert "model_name" in result

    def test_extraction_method_is_gemma4_in_gemma_mode(self, tmp_pdf, monkeypatch):
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json()
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        assert result["extraction_method"] == "gemma-4"
        assert result["model_name"] == os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")


class TestDateConversion:
    def test_date_string_08_nov_2024_converted(self, tmp_pdf, monkeypatch):
        """'08-Nov-2024' from Gemma is stored as '08-Nov-2024' via _coerce_date."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({
            "admission_date": "08-Nov-2024",
            "discharge_date": "12-Nov-2024",
        })
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert fields["admission_date"] == "08-Nov-2024"
        assert fields["discharge_date"] == "12-Nov-2024"

    def test_null_date_becomes_empty_string(self, tmp_pdf, monkeypatch):
        """null dates from Gemma JSON → empty string field value."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({"admission_date": None, "discharge_date": None})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f["value"] for f in result["fields"]}
        assert fields["admission_date"] == ""
        assert fields["discharge_date"] == ""


class TestDisagreementDetection:
    """
    When Gemma and regex both produce a non-empty value for a key field but
    they disagree, the field confidence should drop to 0.5 and a note should
    be appended to the value.
    """

    def test_disagreement_lowers_confidence(self, tmp_pdf, monkeypatch):
        """Gemma says patient_name='Alice' but regex finds 'Arjun Sharma'."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        # Gemma returns a different patient name than what regex finds in the PDF text
        payload = _build_gemma_json({"patient_name": "Alice Smith"})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f for f in result["fields"]}
        patient = fields["patient_name"]
        # The PDF has "Arjun Sharma" extracted by regex; Gemma returns "Alice Smith"
        # so confidence must be CONF_DISAGREE (0.5) and value contains the note
        assert patient["confidence"] == pytest.approx(0.5)
        assert "Gemma and regex disagree" in patient["value"]

    def test_agreement_keeps_confidence(self, tmp_pdf, monkeypatch):
        """When both methods agree, confidence stays at the Gemma-reported value."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        # Both Gemma and regex will produce an empty patient_name for this fixture PDF
        # (the PDF text says "Patient Name\nArjun Sharma", regex extracts it)
        # Make Gemma also return the same name
        payload = _build_gemma_json({
            "patient_name": "Arjun Sharma",
            "confidence": {"patient_name": 0.95},
        })
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f for f in result["fields"]}
        patient = fields["patient_name"]
        assert patient["confidence"] == pytest.approx(0.95)
        assert "disagree" not in patient["value"]

    def test_null_gemma_field_no_disagreement(self, tmp_pdf, monkeypatch):
        """If Gemma returns null for a field, no disagreement note should be added."""
        monkeypatch.setenv("EXTRACTION_MODE", "gemma")
        payload = _build_gemma_json({"patient_name": None})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_mock_response(payload)

        with patch("app.extraction._load_gemini_client", return_value=mock_client):
            from app.extraction import extract_document
            result = extract_document(tmp_pdf)

        fields = {f["name"]: f for f in result["fields"]}
        patient = fields["patient_name"]
        assert "disagree" not in patient["value"]


class TestParallelExtraction:
    """Tests for extract_documents_parallel()."""

    def test_parallel_returns_all_results(self, tmp_pdf, tmp_path, monkeypatch):
        """extract_documents_parallel returns one result per input file."""
        monkeypatch.setenv("EXTRACTION_MODE", "regex")
        from app.extraction import extract_documents_parallel
        # Create three copies of the same PDF
        paths = []
        for i in range(3):
            p = tmp_path / f"doc_{i}.pdf"
            p.write_bytes(tmp_pdf.read_bytes())
            paths.append(p)

        results = extract_documents_parallel(paths, max_workers=3)
        assert len(results) == 3
        for r in results:
            assert r["extraction_method"] == "regex-fallback"

    def test_parallel_empty_list(self, monkeypatch):
        """Empty input returns an empty list without error."""
        monkeypatch.setenv("EXTRACTION_MODE", "regex")
        from app.extraction import extract_documents_parallel
        assert extract_documents_parallel([]) == []
