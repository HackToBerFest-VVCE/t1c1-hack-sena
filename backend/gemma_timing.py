import time, pymupdf
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client()
page = pymupdf.open(r"..\sample_data\bundle_001\hospital_bill.pdf")[0]
img = page.get_pixmap(dpi=100).tobytes("png")
prompt = "Return only JSON with patient_name, bill_number, total_amount and line_items from this bill."
cfg = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    max_output_tokens=1200)

for model in ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]:
    for i in range(4):
        t = time.time()
        try:
            client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=img, mime_type="image/png"), prompt],
                config=cfg)
            print(f"{model} #{i+1}: OK {time.time()-t:.1f}s")
        except Exception as e:
            print(f"{model} #{i+1}: ERR {time.time()-t:.1f}s {str(e)[:60]}")