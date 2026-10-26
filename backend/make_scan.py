import io, random, sys
from pathlib import Path
import pymupdf
from PIL import Image, ImageFilter, ImageEnhance

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.parent.mkdir(parents=True, exist_ok=True)
page = pymupdf.open(src)[0]
img = Image.open(io.BytesIO(page.get_pixmap(dpi=110).tobytes("png"))).convert("L")
img = img.rotate(random.uniform(-2.5, 2.5), expand=True, fillcolor=255)
img = Image.blend(img, Image.effect_noise(img.size, 18), 0.08)
img = img.filter(ImageFilter.GaussianBlur(0.7))
img = ImageEnhance.Contrast(img).enhance(0.9)
img.save(out, "JPEG", quality=55)
print("saved", out)