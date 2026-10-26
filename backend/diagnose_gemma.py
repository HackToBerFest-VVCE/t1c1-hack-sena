import time, pymupdf
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client()
page = pymupdf.open(r"..\sample_data\bundle_001\hospital_bill.pdf")[0]
png = page.get_pixmap(dpi=100).tobytes("png")
jpg = page.get_pixmap(dpi=100).tobytes("jpeg")
prompt = "Return only JSON with patient_name, bill_number and total_amount from this bill."

def run(label, model, contents, config=None):
    for attempt in range(3):
        try:
            r = client.models.generate_content(model=model, contents=contents, config=config)
            print(f"[OK ] {label}: {r.text[:150]!r}")
            return
        except Exception as e:
            print(f"[ERR] {label} (try {attempt+1}): {str(e)[:120]}")
            time.sleep(3)

for m in ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]:
    run(f"{m} | text only", m, "Say hello in one word.")
    run(f"{m} | image png", m, [types.Part.from_bytes(data=png, mime_type="image/png"), prompt])
    run(f"{m} | image jpeg", m, [types.Part.from_bytes(data=jpg, mime_type="image/jpeg"), prompt])
    run(f"{m} | png + minimal thinking", m,
        [types.Part.from_bytes(data=png, mime_type="image/png"), prompt],
        types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="minimal")))