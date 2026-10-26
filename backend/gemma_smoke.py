import pymupdf
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client()  # reads GEMINI_API_KEY from the environment
page = pymupdf.open(r"..\sample_data\bundle_001\hospital_bill.pdf")[0]
img = page.get_pixmap(dpi=150).tobytes("png")
resp = client.models.generate_content(
   model="gemma-4-26b-a4b-it",
    contents=[types.Part.from_bytes(data=img, mime_type="image/png"),
                 "Return only JSON with patient_name, bill_number and total_amount from this bill."],
   )
print(resp.text)