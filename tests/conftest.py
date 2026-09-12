import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
SYNTH_DIR = ROOT / "synth" / "package"


def _text_page(buf: io.BytesIO, lines: list[str]):
    c = canvas.Canvas(buf, pagesize=letter)
    y = 720
    for line in lines:
        c.setFont("Helvetica", 11)
        c.drawString(72, y, line)
        y -= 16
    c.showPage()
    c.save()


def _image_page(buf: io.BytesIO, lines: list[str]):
    img = Image.new("L", (1275, 1650), 255)  # 150 dpi letter
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    y = 120
    for line in lines:
        d.text((120, y), line, fill=0, font=font)
        y += 48
    ib = io.BytesIO()
    img.save(ib, format="PNG")
    ib.seek(0)
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawImage(ImageReader(ib), 0, 0, width=letter[0], height=letter[1])
    c.showPage()
    c.save()


def make_pdf(path: Path, pages: list[tuple[str, list[str]]]) -> Path:
    """pages: list of (kind, lines) where kind is 'text' or 'image'."""
    writer = PdfWriter()
    for kind, lines in pages:
        buf = io.BytesIO()
        (_text_page if kind == "text" else _image_page)(buf, lines)
        buf.seek(0)
        writer.add_page(PdfReader(buf).pages[0])
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def tiny_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    make_pdf(pkg / "minutes.pdf", [
        ("text", ["Strata Plan BCS9999 - Council Meeting Minutes - 14 April 2026",
                  "MINUTES OF THE STRATA COUNCIL MEETING",
                  "Held on 14 April 2026. Council members present: A, B, C.",
                  "4. Roof replacement. Three quotes were reviewed: $780,000, $850,000 and $940,000.",
                  "Council resolved to defer the decision to the annual general meeting."]),
        ("image", ["Strata Plan BCS9999 - Council Meeting Minutes - 14 April 2026",
                   "5. Landscaping. The cedars will be replaced.",
                   "6. Adjournment. The meeting adjourned at 8:40 pm."]),
    ])
    make_pdf(pkg / "formb.pdf", [
        ("text", ["FORM B", "INFORMATION CERTIFICATE", "(Section 59) Strata Property Act",
                  "Strata Lot 12, Suite 304. Monthly strata fees: $410.00.",
                  "Amount owing by the owner: $0.00. Parking stall 8 limited common property."]),
    ])
    return pkg


@pytest.fixture
def tiny_zip(tiny_package: Path, tmp_path: Path) -> Path:
    z = tmp_path / "pkg.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in tiny_package.glob("*.pdf"):
            zf.write(f, arcname=f"folder/{f.name}")
    return z


@pytest.fixture(scope="session")
def synth_package() -> Path:
    """The generated synthetic building. Generated on demand if missing."""
    if not any(SYNTH_DIR.glob("*.pdf")):
        from synth.generate import generate
        generate(SYNTH_DIR)
    return SYNTH_DIR
