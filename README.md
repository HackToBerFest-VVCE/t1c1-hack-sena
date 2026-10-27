ClaimLens AI

Evidence-based multimodal claim review assistant for health insurance reviewers.

Hacktoberfest x VectorFlow 2026 | VVCE Mysuru | Track 1, Challenge A: Best Use of Gemma 4 Team: Hack Sena (Preetham Santhosh, Deepika V S)

Demo system. All data is synthetic. No real patient documents are used.

AI model used
	
Model	Google Gemma 4 (gemma-4-26b-a4b-it)
Served through	Gemini API (Google AI Studio key), using the google-genai Python SDK
Used for	Reading page images of claim documents and returning structured JSON with a confidence score per field
Code	backend/app/extraction.py

Gemma 4 receives each document page as an image plus a JSON-only prompt. It classifies the document, extracts fields, line items and signature status, and returns null for anything it cannot see. The output is cleaned, validated with Pydantic and passed to the rule engine. Gemma 4 never makes a claim decision.

The model name is set with the GEMMA_MODEL environment variable, so it can be changed without touching code.

The problem

Healthcare insurance claims frequently contain missing information, inconsistent billing details, duplicate submissions, or discrepancies between medical reports and claim forms. Manually reviewing multiple documents is time-consuming and can delay claim processing. There is a need for a multimodal AI system that can extract information from healthcare documents, identify potential inconsistencies, and provide evidence-based explanations to support insurance claim reviewers.

How ClaimLens AI works
Upload claim bundle (PDF / scan / photo)
        |
PyMuPDF renders pages to images
        |
Gemma 4 (Gemini API) --> structured JSON + confidence per field
        |                 (regex fallback if the API fails; results cached by file hash)
Python rule engine --> validation checks + fraud risk indicators
        |
Risk score (Low / Medium / High) with reasons
        |
Findings with evidence (document + page) --> Reviewer decides
                                              (Approve / Query / Escalate)

Principles

AI extracts, rules decide. Checks are deterministic Python, so results are repeatable and explainable.
Evidence for everything. Every finding names its source document and page, why it matters and a suggested action.
Human in control. Reviewers can edit any field and choose Approve, Query or Escalate. There is no automatic approval or rejection.
Risk indicators, not verdicts. Fraud signals are labelled "risk indicators". The system never states that fraud is confirmed.
Checks performed
ID	Check
V1	Line items do not add up to the printed bill total
V2	Claimed amount differs from the billed total
V3	Patient name differs across documents (fuzzy match)
V4	Date inconsistencies (for example admission after discharge)
V5	Missing required fields per document type (including a missing signature)
V6	Admission or discharge before the policy start date
F1	Near-duplicate of an earlier claim (patient, hospital, dates, amount)
F2	Same file hash as an earlier claim
F2B	Same bill number and hospital as an earlier claim
F3	Round-number billing (most line items end in .00)

Weights and thresholds are our own assumptions and are meant to be tuned on real data.

Features
Multimodal extraction with Gemma 4 from PDFs, scans and photos
Per-document-type fields, with confidence indicators and click-to-edit corrections
Risk-ranked claims queue with reasons behind each score
Evidence viewer: document pages beside extracted fields, findings link to the exact page
Reviewer decisions (Approve, Query, Escalate) with comments
Audit log of uploads, corrections and decisions
Resilience: retries, regex fallback and file-hash caching when the hosted API is slow or fails
Tech stack
Layer	Technology
AI	Gemma 4 via Gemini API, google-genai
Backend	Python, FastAPI, Uvicorn, Pydantic
Database	SQLite with SQLAlchemy
Documents	PyMuPDF
Rule engine	Python, RapidFuzz
Frontend	React, TypeScript, Vite, Axios, React Router
Testing	pytest (Gemini client mocked, no real API calls in tests)
Test data	Python script with reportlab
Project structure
claimlens-ai/
  backend/
    app/              FastAPI app, extraction, rules, models
    tests/            pytest suite
  frontend/           React + Vite app
  sample_data/        synthetic claim bundles + ground_truth.json
Setup (Windows PowerShell)

Prerequisites: Python 3.11+, Node.js 20+, a Gemini API key from https://aistudio.google.com/apikey

1. Backend
powershell
cd backend
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

Create backend/.env (this file is git-ignored; never commit it):

GEMINI_API_KEY=your-key-here
EXTRACTION_MODE=gemma
GEMMA_MODEL=gemma-4-26b-a4b-it

Use EXTRACTION_MODE=regex to run without any API calls (text extraction only).

Start the server:

powershell
uvicorn app.main:app --reload

Check http://localhost:8000/health. It shows the extraction mode and whether a key is set. API docs: http://localhost:8000/docs.

2. Frontend
powershell
cd frontend
npm install
npm run dev

Open http://localhost:5173.

3. Load the demo data

Upload a bundle from sample_data/bundle_001 (claim form, hospital bill, discharge summary) on the Upload page. Load bundles in order, because duplicate detection compares against earlier claims. Bundle 008 is a deliberate duplicate of bundle 001.

4. Run the tests
powershell
cd backend
pytest -q
Synthetic test data

sample_data/ contains 8 generated claim bundles. ground_truth.json records the planted problem in each one:

Bundle	Planted problem
001	None (clean)
002	Claimed amount above billed total
003	Bill total differs from line items
004	Missing signature
005	Patient name mismatch
006	Treatment before policy start
007	Round-number billing
008	Duplicate of bundle 001
Results so far
On the 8 synthetic bundles the rule engine flagged 7 of 7 planted problems and raised no findings on the clean bundle. This run used text extraction (baseline).
A Gemma 4 vs text-extraction accuracy comparison is still to be measured. This README will be updated with those numbers.
Gemma 4 correctly read a sample hospital bill image (patient name, bill number and total) as JSON in our integration test.
Limitations
Hosted inference. Gemma 4 runs through the Gemini API, so page images leave the machine. That is why only synthetic data is used. The weights are open, so self-hosting is the path for real claims (not built or tested here).
Latency and reliability. In testing, hosted calls varied from about 10 to over 60 seconds and sometimes returned server errors. Retries, caching and the regex fallback reduce the impact.
Small synthetic test set. Results are not evidence of real-world accuracy.
Model can misread. Poor scans or handwriting can produce wrong values. The reviewer sees the source page beside every field and can correct it.
Prototype security. Not hardened for real use.
Partial fraud coverage. It flags indicators such as duplicates and billing patterns. It cannot detect skilled forgeries or collusion.
Planned next

Plain-language claim summary, a claim Q&A assistant grounded in the claim's own documents, policy-holder and service-price checks, and a self-hosted deployment option.

Security note

The API key is read from backend/.env and is never committed. Do not paste it into code, screenshots or issues. If it is ever exposed, delete it in Google AI Studio and create a new one.
