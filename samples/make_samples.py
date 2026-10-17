"""Generate the demo document set for a FICTIONAL fastener exporter.

Creates realistic scans/photos and PDFs plus a ground-truth extraction for each
(samples/golden/<sha256>.json), used only to replay a sample if Gemini cannot
be reached during a live demo.

All companies, people, numbers and addresses are invented.
Run:  python samples/make_samples.py
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
GOLD = OUT / "golden"
GOLD.mkdir(exist_ok=True)

F_SANS = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
F_SANS_B = "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
F_SERIF = "/usr/share/fonts/truetype/freefont/FreeSerif.ttf"
F_SERIF_I = "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf"
F_MONO = "/usr/share/fonts/truetype/freefont/FreeMono.ttf"

BUYER = "Verkam Fasteners Pvt. Ltd."
BUYER_ADDR = "Plot 41, Focal Point Phase VIII, Ludhiana, Punjab 141010"
BUYER_GST = "03AACCV4821K1Z7"

manifest: list[dict] = []


def font(path, size):
    return ImageFont.truetype(path, size)


# ---------------------------------------------------------------------------
# Raster "photo" documents
# ---------------------------------------------------------------------------

class Page:
    def __init__(self, w=1240, h=1754, bg=(252, 251, 247)):
        self.im = Image.new("RGB", (w, h), bg)
        self.d = ImageDraw.Draw(self.im)
        self.w, self.h = w, h

    def text(self, xy, s, size=26, f=F_SANS, fill=(25, 25, 30), anchor="la"):
        self.d.text(xy, s, font=font(f, size), fill=fill, anchor=anchor)

    def line(self, a, b, width=2, fill=(40, 40, 40)):
        self.d.line([a, b], fill=fill, width=width)

    def rect(self, box, width=2, outline=(40, 40, 40), fill=None):
        self.d.rectangle(box, outline=outline, width=width, fill=fill)

    def table(self, x, y, cols, rows, size=24, row_h=46, header_fill=(235, 235, 235), f=F_SANS):
        total = sum(c[1] for c in cols)
        self.rect((x, y, x + total, y + row_h * (len(rows) + 1)), 2)
        self.d.rectangle((x + 2, y + 2, x + total - 2, y + row_h - 2), fill=header_fill)
        cx = x
        for name, wdt in cols:
            self.text((cx + 10, y + 12), name, size - 2, F_SANS_B)
            cx += wdt
            self.line((cx, y), (cx, y + row_h * (len(rows) + 1)), 1)
        for i, row in enumerate(rows):
            ry = y + row_h * (i + 1)
            self.line((x, ry), (x + total, ry), 1)
            cx = x
            for (name, wdt), val in zip(cols, row):
                self.text((cx + 10, ry + 12), str(val), size, f)
                cx += wdt
        return y + row_h * (len(rows) + 1)


def stamp(p: Page, center, text1, text2, color=(30, 60, 160)):
    x, y = center
    r = 95
    layer = Image.new("RGBA", p.im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((x - r, y - r, x + r, y + r), outline=color + (170,), width=5)
    d.ellipse((x - r + 14, y - r + 14, x + r - 14, y + r - 14), outline=color + (150,), width=2)
    d.text((x, y - 18), text1, font=font(F_SANS_B, 20), fill=color + (170,), anchor="mm")
    d.text((x, y + 16), text2, font=font(F_SANS, 18), fill=color + (170,), anchor="mm")
    layer = layer.rotate(-14, center=center)
    p.im.paste(layer, (0, 0), layer)


def handwrite(p: Page, xy, s, size=34, color=(20, 40, 120), rng=None):
    rng = rng or random.Random(1)
    x, y = xy
    for ch in s:
        f = font(F_SERIF_I, size + rng.randint(-2, 2))
        dy = rng.randint(-3, 3)
        p.d.text((x, y + dy), ch, font=f, fill=color)
        x += f.getlength(ch) + rng.uniform(-0.5, 1.5)
    return x


def photograph(page: Page, seed: int, angle: float, warp: float = 0.03, shadow: bool = True) -> bytes:
    """Make a clean page look like a phone photo of paper on a desk."""
    rng = random.Random(seed)
    im = page.im
    # paper grain
    noise = Image.effect_noise(im.size, 10).convert("L")
    im = Image.blend(im, Image.merge("RGB", (noise, noise, noise)), 0.05)
    # light gradient (uneven lighting)
    grad = Image.linear_gradient("L").resize(im.size).rotate(rng.choice([30, 120, 210]), expand=False)
    im = Image.composite(im, ImageOps.colorize(grad, (150, 145, 135), (255, 255, 255)).convert("RGB"), grad.point(lambda v: 200 + v // 5))
    # put on a desk
    W, H = int(im.width * 1.18), int(im.height * 1.12)
    desk = Image.new("RGB", (W, H), (104, 86, 70))
    desk = Image.blend(desk, Image.effect_noise((W, H), 40).convert("RGB"), 0.12)
    if shadow:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle((int(W * 0.09) + 18, int(H * 0.06) + 22, int(W * 0.09) + im.width + 18, int(H * 0.06) + im.height + 22), fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(18))
        desk.paste(sh, (0, 0), sh)
    desk.paste(im, (int(W * 0.09), int(H * 0.06)))
    desk = desk.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(104, 86, 70))
    # mild perspective
    w, h = desk.size
    dx = int(w * warp)
    coeffs = _persp_coeffs([(0, 0), (w, 0), (w, h), (0, h)], [(dx, 0), (w - dx // 2, dx // 3), (w, h), (0, h - dx // 2)])
    desk = desk.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(104, 86, 70))
    desk = desk.resize((int(w * 0.8), int(h * 0.8)), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    desk.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def _persp_coeffs(src, dst):
    import numpy as np
    m = []
    for (x, y), (u, v) in zip(dst, src):
        m.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        m.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(m, dtype=float)
    B = np.array([c for p in src for c in p], dtype=float)
    return np.linalg.solve(A, B).tolist()


def save(name: str, data: bytes, mime: str, title: str, golden: dict, stage: str = "initial", kind: str = ""):
    (OUT / name).write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    (GOLD / f"{sha}.json").write_text(json.dumps(golden, indent=1))
    manifest.append({"file": name, "mime": mime, "title": title, "stage": stage, "kind": kind, "sha256": sha})


def item(kind, desc, qty, unit, quote, page=1, **kw):
    d = {"kind": kind, "description": desc, "quantity": qty, "unit": unit, "source_quote": quote, "page": page, "confidence": kw.pop("confidence", 0.95)}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# 1. LPG invoices (two photos in kg, one PDF in MT)
# ---------------------------------------------------------------------------

def lpg_photo(fname, inv_no, date, qty_kg, rate, seed, angle):
    p = Page()
    p.text((80, 70), "SATLUJ GAS SERVICES", 46, F_SANS_B, fill=(150, 30, 30))
    p.text((80, 128), "Authorised Bulk LPG Distributor (fictional)  |  G.T. Road, Doraha, Punjab", 22)
    p.text((80, 160), "GSTIN 03ABDFS7713M1ZQ  |  Ph 0161-55XXXXX", 22)
    p.line((80, 205), (1160, 205), 3)
    p.text((620, 235), "TAX INVOICE", 36, F_SANS_B, anchor="ma")
    p.text((80, 300), f"Invoice No: {inv_no}", 26)
    p.text((760, 300), f"Date: {date}", 26)
    p.text((80, 345), f"Bill to: {BUYER}", 26, F_SANS_B)
    p.text((80, 382), BUYER_ADDR, 22)
    p.text((80, 414), f"GSTIN {BUYER_GST}", 22)
    p.text((80, 446), "Delivery: bulk tanker to customer LPG storage (heat treatment furnace)", 22)
    y = p.table(80, 500, [("Description", 470), ("HSN", 130), ("Qty", 160), ("Unit", 90), ("Rate", 120), ("Amount", 110)], [
        ["LPG (bulk) for industrial use", "27111900", f"{qty_kg:,}", "KG", f"{rate:.2f}", f"{qty_kg * rate / 100000:.2f} L"],
        ["Tanker freight", "996511", "1", "TRIP", "4500.00", "0.05 L"],
    ], size=22)
    p.text((80, y + 30), "Amount in INR lakh (L). GST @ 18% extra as applicable.", 20)
    p.text((80, y + 70), "Fuel use declared by customer: Hardening & tempering furnace (mesh belt).", 22)
    p.text((80, y + 130), "Density at 15°C: 0.54 kg/L (for reference)", 20)
    p.text((820, 1500), "For SATLUJ GAS SERVICES", 22)
    p.text((860, 1620), "Authorised Signatory", 22)
    stamp(p, (930, 1560), "SATLUJ GAS", "DORAHA")
    handwrite(p, (850, 1585), "R. Singh", 30)
    data = photograph(p, seed, angle)
    month = date.split("-")[1]
    save(fname, data, "image/jpeg", f"LPG invoice {inv_no} (photo)", {
        "doc_type": "fuel_invoice", "issuer": "Satluj Gas Services", "recipient": BUYER, "document_number": inv_no,
        "document_date": f"2026-{month}-{date.split('-')[0]}", "period_start": None, "period_end": None, "languages": ["English"],
        "line_items": [item("fuel", "LPG (bulk) for industrial use - hardening & tempering furnace", qty_kg, "KG",
                            f"LPG (bulk) for industrial use 27111900 {qty_kg:,} KG", fuel_type="lpg", fuel_use="process_heat",
                            period_start=f"2026-{month}-{date.split('-')[0]}", period_end=f"2026-{month}-{date.split('-')[0]}")],
        "reading_notes": ["Photo slightly skewed; all figures legible."]}, kind="fuel")


def lpg_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setFillColor(colors.HexColor("#962020"))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(20 * mm, H - 25 * mm, "SATLUJ GAS SERVICES")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, H - 31 * mm, "Authorised Bulk LPG Distributor (fictional) | G.T. Road, Doraha, Punjab | GSTIN 03ABDFS7713M1ZQ")
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(W / 2, H - 45 * mm, "TAX INVOICE (e-Invoice)")
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, H - 58 * mm, "Invoice No: SGS/26-27/0612")
    c.drawString(130 * mm, H - 58 * mm, "Date: 14-06-2026")
    c.drawString(20 * mm, H - 66 * mm, f"Bill to: {BUYER}, {BUYER_ADDR}")
    c.drawString(20 * mm, H - 72 * mm, f"GSTIN {BUYER_GST}   Place of supply: Punjab (03)")
    rows = [["Description", "HSN", "Qty", "UOM", "Rate (Rs/MT)", "Taxable value (Rs)"],
            ["LPG (bulk) for industrial use - furnace", "27111900", "18.500", "MT", "68,400.00", "12,65,400.00"],
            ["Tanker freight", "996511", "1", "TRIP", "4,500.00", "4,500.00"]]
    y = H - 90 * mm
    xs = [20, 95, 120, 140, 155, 180]
    c.setFont("Helvetica-Bold", 9)
    for i, row in enumerate(rows):
        if i == 1:
            c.setFont("Helvetica", 9)
        for x, v in zip(xs, row):
            c.drawString(x * mm, y, v)
        y -= 8 * mm
    c.line(20 * mm, H - 86 * mm, 195 * mm, H - 86 * mm)
    c.drawString(20 * mm, y - 6 * mm, "Quantity measured by mass flow meter at delivery. Use: hardening & tempering furnace.")
    c.drawString(20 * mm, y - 14 * mm, "IGST/CGST/SGST as applicable. This is a computer generated invoice.")
    c.showPage()
    c.save()
    save("04_lpg_invoice_jun.pdf", buf.getvalue(), "application/pdf", "LPG e-invoice June (PDF)", {
        "doc_type": "fuel_invoice", "issuer": "Satluj Gas Services", "recipient": BUYER, "document_number": "SGS/26-27/0612",
        "document_date": "2026-06-14", "languages": ["English"],
        "line_items": [item("fuel", "LPG (bulk) for industrial use - furnace", 18.5, "MT", "LPG (bulk) for industrial use - furnace 27111900 18.500 MT",
                            fuel_type="lpg", fuel_use="process_heat", period_start="2026-06-14", period_end="2026-06-14")],
        "reading_notes": []}, kind="fuel")


# ---------------------------------------------------------------------------
# 2. Diesel for DG set (photo of a fuel-station bill)
# ---------------------------------------------------------------------------

def diesel_photo():
    p = Page(1000, 1500, (250, 250, 244))
    p.text((500, 60), "GURU NANAK FILLING STATION", 40, F_SANS_B, anchor="ma")
    p.text((500, 112), "(Dealer, fictional) Focal Point Road, Ludhiana", 22, anchor="ma")
    p.text((500, 146), "GSTIN 03AEJPG2209C1Z4", 20, anchor="ma")
    p.line((60, 185), (940, 185), 2)
    p.text((60, 210), "CREDIT BILL - MONTHLY STATEMENT", 28, F_SANS_B)
    p.text((60, 258), f"Party: {BUYER}", 24)
    p.text((60, 292), "Vehicle / Purpose: DG SET 500 kVA (barrel delivery)", 24, F_SANS_B)
    p.text((60, 326), "Period: 01-04-2026 to 30-06-2026", 24)
    y = p.table(60, 380, [("Date", 190), ("Product", 200), ("Litres", 170), ("Rate", 140), ("Amount", 180)], [
        ["08-04-2026", "HSD", "3,200", "94.10", "3,01,120"],
        ["21-05-2026", "HSD", "3,400", "94.35", "3,20,790"],
        ["17-06-2026", "HSD", "3,000", "94.60", "2,83,800"],
        ["", "TOTAL", "9,600", "", "9,05,710"],
    ], size=22)
    p.text((60, y + 30), "HSD = High Speed Diesel. Rates incl. taxes.", 20)
    handwrite(p, (60, y + 90), "All 3 deliveries for DG set only - not vehicles", 30, rng=random.Random(7))
    p.text((640, 1330), "Manager", 22)
    stamp(p, (760, 1250), "GURU NANAK F.S.", "LUDHIANA", (120, 30, 120))
    data = photograph(p, 11, -2.2)
    save("05_diesel_dg_statement.jpg", data, "image/jpeg", "Diesel statement for DG set (photo)", {
        "doc_type": "fuel_invoice", "issuer": "Guru Nanak Filling Station", "recipient": BUYER, "document_number": None,
        "document_date": "2026-06-30", "period_start": "2026-04-01", "period_end": "2026-06-30", "languages": ["English"],
        "line_items": [
            item("fuel", "HSD for DG set 500 kVA", 3200, "Litres", "08-04-2026 HSD 3,200 94.10", fuel_type="diesel", fuel_use="electricity_generation", period_start="2026-04-08", period_end="2026-04-08"),
            item("fuel", "HSD for DG set 500 kVA", 3400, "Litres", "21-05-2026 HSD 3,400 94.35", fuel_type="diesel", fuel_use="electricity_generation", period_start="2026-05-21", period_end="2026-05-21"),
            item("fuel", "HSD for DG set 500 kVA", 3000, "Litres", "17-06-2026 HSD 3,000 94.60", fuel_type="diesel", fuel_use="electricity_generation", period_start="2026-06-17", period_end="2026-06-17"),
        ],
        "reading_notes": ["Handwritten note confirms all deliveries were for the DG set."]}, kind="fuel")


# ---------------------------------------------------------------------------
# 3. Electricity bill (PDF, June only)
# ---------------------------------------------------------------------------

def electricity_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setFillColor(colors.HexColor("#0B4F8A"))
    c.rect(0, H - 30 * mm, W, 30 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(15 * mm, H - 15 * mm, "PUNJAB POWER DISTRIBUTION CO. (SAMPLE / FICTIONAL)")
    c.setFont("Helvetica", 9)
    c.drawString(15 * mm, H - 22 * mm, "Large Supply (LS) Industrial - Bill of Supply")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    data = [("Account No", "LS-3302-41178"), ("Consumer", BUYER), ("Address", BUYER_ADDR), ("Sanctioned load", "740 kW / 820 kVA"),
            ("Bill month", "JUN-2026"), ("Reading period", "01-06-2026 to 30-06-2026"), ("Bill date", "05-07-2026")]
    y = H - 42 * mm
    for k, v in data:
        c.drawString(15 * mm, y, f"{k}:")
        c.drawString(55 * mm, y, v)
        y -= 6.5 * mm
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 10)
    for x, h in zip([15, 70, 105, 140, 170], ["Meter", "Previous", "Present", "MF", "Units (kWh)"]):
        c.drawString(x * mm, y, h)
    c.setFont("Helvetica", 10)
    y -= 7 * mm
    for x, v in zip([15, 70, 105, 140, 170], ["kWh main", "1,284,330", "1,288,860", "x 30", "135,900"]):
        c.drawString(x * mm, y, v)
    y -= 12 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15 * mm, y, "Total consumption: 135,900 kWh        kVAh: 141,560        Power factor: 0.96")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    for k, v in [("Energy charges", "Rs 10,46,430.00"), ("Fixed charges", "Rs 1,23,000.00"), ("Electricity duty & cess", "Rs 1,36,200.00"), ("Net payable", "Rs 13,05,630.00")]:
        c.drawString(15 * mm, y, k)
        c.drawRightString(190 * mm, y, v)
        y -= 6.5 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(15 * mm, 20 * mm, "Sample document generated for a software demo. Not a real bill.")
    c.showPage()
    c.save()
    save("06_electricity_bill_jun.pdf", buf.getvalue(), "application/pdf", "Electricity bill June (PDF)", {
        "doc_type": "electricity_bill", "issuer": "Punjab Power Distribution Co. (sample)", "recipient": BUYER, "document_number": "LS-3302-41178",
        "document_date": "2026-07-05", "period_start": "2026-06-01", "period_end": "2026-06-30", "languages": ["English"],
        "line_items": [item("electricity", "Grid electricity, kWh main meter", 135900, "kWh", "Total consumption: 135,900 kWh",
                            period_start="2026-06-01", period_end="2026-06-30")],
        "reading_notes": ["Only the June bill was provided."]}, kind="electricity")


# ---------------------------------------------------------------------------
# 4. Wire rod purchases: mill statement of supplies (PDF)
# ---------------------------------------------------------------------------

def wire_rod_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(18 * mm, H - 25 * mm, "SHIVALIK WIRE & STEEL LTD.")
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, H - 31 * mm, "(fictional) Integrated steel plant & rolling mill | Mandi Gobindgarh, Punjab | GSTIN 03AAECS5521R1ZK")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(18 * mm, H - 45 * mm, "STATEMENT OF SUPPLIES - Q1 FY2026-27 (April-June 2026)")
    c.setFont("Helvetica", 10)
    c.drawString(18 * mm, H - 53 * mm, f"Customer: {BUYER} ({BUYER_GST})")
    c.drawString(18 * mm, H - 59 * mm, "Product: Hot rolled steel wire rod in coils, SAE 1018 / 10B21, 5.5-14 mm, HSN 72139190")
    hdr = ["Invoice No", "Date", "Grade / size", "HSN", "Net wt (MT)", "Value (Rs lakh)"]
    rows = [["SWS/WR/1123", "11-04-2026", "SAE1018 / 8.0 mm", "72139190", "90.000", "51.30"],
            ["SWS/WR/1311", "16-05-2026", "10B21 / 10.0 mm", "72139190", "95.240", "55.24"],
            ["SWS/WR/1502", "12-06-2026", "SAE1018 / 12.0 mm", "72139190", "84.760", "48.74"],
            ["", "", "", "Total", "270.000", "155.28"]]
    xs = [18, 50, 78, 120, 145, 172]
    y = H - 72 * mm
    c.setFont("Helvetica-Bold", 9)
    for x, h in zip(xs, hdr):
        c.drawString(x * mm, y, h)
    c.line(18 * mm, y - 2 * mm, 195 * mm, y - 2 * mm)
    c.setFont("Helvetica", 9)
    for r in rows:
        y -= 7 * mm
        for x, v in zip(xs, r):
            c.drawString(x * mm, y, v)
    y -= 14 * mm
    c.drawString(18 * mm, y, "Route of production at our plant: BF-BOF (blast furnace - basic oxygen furnace) with continuous casting and rolling.")
    y -= 6 * mm
    c.drawString(18 * mm, y, "CBAM installation-level emissions data available on request from: sustainability@shivalik-example.in")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(18 * mm, 20 * mm, "Sample document generated for a software demo. Company is fictional.")
    c.showPage()
    c.save()
    li = [item("precursor_purchase", "Hot rolled steel wire rod SAE1018 8.0 mm", 90.0, "MT", "SWS/WR/1123 11-04-2026 SAE1018 / 8.0 mm 72139190 90.000",
               cn_code="7213 91 90", party="Shivalik Wire & Steel Ltd.", period_start="2026-04-11", period_end="2026-04-11"),
          item("precursor_purchase", "Hot rolled steel wire rod 10B21 10.0 mm", 95.24, "MT", "SWS/WR/1311 16-05-2026 10B21 / 10.0 mm 72139190 95.240",
               cn_code="7213 91 90", party="Shivalik Wire & Steel Ltd.", period_start="2026-05-16", period_end="2026-05-16"),
          item("precursor_purchase", "Hot rolled steel wire rod SAE1018 12.0 mm", 84.76, "MT", "SWS/WR/1502 12-06-2026 SAE1018 / 12.0 mm 72139190 84.760",
               cn_code="7213 91 90", party="Shivalik Wire & Steel Ltd.", period_start="2026-06-12", period_end="2026-06-12")]
    save("01_wire_rod_supplies_apr-jun.pdf", buf.getvalue(), "application/pdf", "Wire rod statement of supplies, Apr-Jun (PDF)", {
        "doc_type": "precursor_purchase_invoice", "issuer": "Shivalik Wire & Steel Ltd.", "recipient": BUYER, "document_number": None,
        "document_date": "2026-06-30", "period_start": "2026-04-01", "period_end": "2026-06-30", "languages": ["English"],
        "line_items": li, "reading_notes": ["Supplier states BF-BOF route; emissions data available on request."]}, kind="precursor")


# ---------------------------------------------------------------------------
# 5. Production register (handwritten, bilingual, photo)
# ---------------------------------------------------------------------------

def register_photo():
    rng = random.Random(42)
    p = Page(1700, 1240, (247, 244, 232))
    # ruled ledger
    for yy in range(140, 1200, 62):
        p.line((40, yy), (1660, yy), 1, (170, 190, 215))
    p.line((150, 40), (150, 1200), 2, (210, 120, 120))
    p.text((60, 40), "उत्पादन रजिस्टर / PRODUCTION REGISTER  —  Plant 1", 38, F_SANS_B, fill=(30, 30, 30))
    p.text((60, 92), "Verkam Fasteners  |  Q1 FY26-27  |  मात्रा: मीट्रिक टन (MT)", 26, F_SANS, fill=(60, 60, 60))
    cols = [("माह Month", 60), ("वायर रॉड जारी\nWire rod issued", 330), ("बोल्ट Bolts\n7318 15", 720), ("नट Nuts\n7318 16", 1010), ("स्क्रैप Scrap", 1290)]
    for name, x in cols:
        for i, part in enumerate(name.split("\n")):
            p.text((x, 170 + i * 34), part, 26, F_SANS_B, fill=(40, 40, 40))
    rows = [("Apr-26", "86.4", "60.1", "20.3", "6.0"), ("May-26", "89.9", "62.5", "21.0", "6.4"), ("Jun-26", "86.0", "59.8", "20.3", "5.9")]
    y = 300
    for r in rows:
        for (name, x), val in zip(cols, r):
            shown = "61.5" if (r[0] == "May-26" and name.startswith("बोल्ट")) else val
            handwrite(p, (x, y), shown, 40, rng=rng)
        y += 124
    # overwritten digit: May bolts originally 61.5 corrected to 62.5
    p.d.line([(720, 425 + 26), (800, 425 + 6)], fill=(20, 40, 120), width=3)
    handwrite(p, (830, 400), "62.5", 30, color=(160, 20, 20), rng=rng)
    p.line((40, y + 10), (1660, y + 10), 3, (60, 60, 60))
    p.text((60, y + 46), "कुल", 34, F_SANS, fill=(20, 40, 120))
    handwrite(p, (130, y + 40), "Total", 38, rng=rng)
    for (name, x), val in zip(cols[1:], ("262.3", "182.4", "61.6", "18.3")):
        handwrite(p, (x, y + 40), val, 40, rng=rng)
    handwrite(p, (60, y + 200), "Checked - Prod. Supervisor  (sd.)  30/6", 32, rng=rng)
    handwrite(p, (1100, y + 200), "Scrap sold to local melter", 28, rng=rng)
    data = photograph(p, 5, 1.6, warp=0.04)
    q = lambda m, v: f"{m} {v}"
    li = []
    for m, (mon, wr, b, n, s) in zip(["04", "05", "06"], rows):
        ps, pe = f"2026-{m}-01", f"2026-{m}-{'30' if m in ('04', '06') else '31'}"
        li += [item("precursor_consumption", "Wire rod issued to production", float(wr), "MT", q(mon, wr), cn_code="7213", period_start=ps, period_end=pe),
               item("production", "Bolts (7318 15)", float(b), "MT", q(mon, b), cn_code="7318 15", period_start=ps, period_end=pe,
                    confidence=0.72 if m == "05" else 0.93),
               item("production", "Nuts (7318 16)", float(n), "MT", q(mon, n), cn_code="7318 16", period_start=ps, period_end=pe),
               item("scrap", "Process scrap", float(s), "MT", q(mon, s), period_start=ps, period_end=pe)]
    save("07_production_register_apr-jun.jpg", data, "image/jpeg", "Handwritten production register, Hindi/English (photo)", {
        "doc_type": "production_register", "issuer": "Verkam Fasteners - Plant 1", "recipient": None, "document_number": None,
        "document_date": "2026-06-30", "period_start": "2026-04-01", "period_end": "2026-06-30", "languages": ["Hindi", "English"],
        "line_items": li, "reading_notes": ["May bolts value overwritten: 61.5 crossed out and corrected to 62.5 in red ink.",
                                            "Total row present (262.3 / 182.4 / 61.6 / 18.3) and not extracted separately."]}, kind="production")


# ---------------------------------------------------------------------------
# 6. Export invoices
# ---------------------------------------------------------------------------

def export_pdf(fname, inv, date, buyer, country, lines, title):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18 * mm, H - 22 * mm, "VERKAM FASTENERS PVT. LTD.")
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, H - 28 * mm, f"(fictional) {BUYER_ADDR} | IEC 0316XXXXX | GSTIN {BUYER_GST}")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(18 * mm, H - 42 * mm, "COMMERCIAL INVOICE / EXPORT")
    c.setFont("Helvetica", 10)
    for i, (k, v) in enumerate([("Invoice No", inv), ("Date", date), ("Consignee", buyer), ("Country of final destination", country),
                                ("Country of origin", "India"), ("Incoterms", "FOB Nhava Sheva")]):
        c.drawString(18 * mm, H - (52 + 6.5 * i) * mm, f"{k}:")
        c.drawString(70 * mm, H - (52 + 6.5 * i) * mm, v)
    y = H - 100 * mm
    c.setFont("Helvetica-Bold", 9)
    for x, h in zip([18, 95, 125, 150, 172], ["Description of goods", "HS code", "Net wt (MT)", "Pallets", "Value (EUR)"]):
        c.drawString(x * mm, y, h)
    c.line(18 * mm, y - 2 * mm, 195 * mm, y - 2 * mm)
    c.setFont("Helvetica", 9)
    for desc, hs, wt, pal, val in lines:
        y -= 7 * mm
        for x, v in zip([18, 95, 125, 150, 172], [desc, hs, f"{wt:.3f}", str(pal), f"{val:,.2f}"]):
            c.drawString(x * mm, y, v)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(18 * mm, 20 * mm, "Sample document generated for a software demo. All parties are fictional.")
    c.showPage()
    c.save()
    li = [item("export", d, w, "MT", f"{d} {hs} {w:.3f}", cn_code=hs, destination_country=country, party=buyer,
               period_start=_iso(date), period_end=_iso(date)) for d, hs, w, _, _ in lines]
    save(fname, buf.getvalue(), "application/pdf", title, {
        "doc_type": "export_invoice", "issuer": BUYER, "recipient": buyer, "document_number": inv, "document_date": _iso(date),
        "languages": ["English"], "line_items": li, "reading_notes": []}, kind="export")


def export_photo():
    p = Page()
    p.text((80, 70), "VERKAM FASTENERS PVT. LTD.", 42, F_SANS_B)
    p.text((80, 125), f"(fictional) {BUYER_ADDR}", 22)
    p.line((80, 170), (1160, 170), 3)
    p.text((80, 200), "COMMERCIAL INVOICE / EXPORT", 32, F_SANS_B)
    for i, (k, v) in enumerate([("Invoice No", "VF/EXP/26-27/031"), ("Date", "02-06-2026"), ("Consignee", "Zaklady Laczne Wisla Sp. z o.o. (fictional)"),
                                ("Final destination", "Poland"), ("Origin", "India")]):
        p.text((80, 270 + i * 44), f"{k}:", 26, F_SANS_B)
        p.text((420, 270 + i * 44), v, 26)
    p.table(80, 520, [("Description", 470), ("HS code", 180), ("Net wt (MT)", 200), ("Value (EUR)", 230)], [
        ["Hex bolts M10-M16, 8.8, zinc plated", "7318 15", "40.000", "63,200.00"],
        ["Hex nuts M10-M16, class 8", "7318 16", "15.000", "21,750.00"],
    ], size=22)
    p.text((80, 720), "Packing: 22 pallets. Net weight excludes pallets and cartons.", 22)
    stamp(p, (900, 1500), "VERKAM", "EXPORTS")
    data = photograph(p, 21, 2.8)
    save("09_export_invoice_pl.jpg", data, "image/jpeg", "Export invoice to Poland (photo)", {
        "doc_type": "export_invoice", "issuer": BUYER, "recipient": "Zaklady Laczne Wisla Sp. z o.o.", "document_number": "VF/EXP/26-27/031",
        "document_date": "2026-06-02", "languages": ["English"],
        "line_items": [item("export", "Hex bolts M10-M16, 8.8, zinc plated", 40.0, "MT", "Hex bolts M10-M16, 8.8, zinc plated 7318 15 40.000",
                            cn_code="7318 15", destination_country="Poland", period_start="2026-06-02", period_end="2026-06-02"),
                       item("export", "Hex nuts M10-M16, class 8", 15.0, "MT", "Hex nuts M10-M16, class 8 7318 16 15.000",
                            cn_code="7318 16", destination_country="Poland", period_start="2026-06-02", period_end="2026-06-02")],
        "reading_notes": []}, kind="export")


def _iso(d):
    dd, mm_, yy = d.split("-")
    return f"{yy}-{mm_}-{dd}"


# ---------------------------------------------------------------------------
# 7. Supplier emissions declaration (stage 2 of the demo)
# ---------------------------------------------------------------------------

def supplier_declaration_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18 * mm, H - 22 * mm, "SHIVALIK WIRE & STEEL LTD.")
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, H - 28 * mm, "(fictional) Mandi Gobindgarh, Punjab | Sustainability Cell")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(18 * mm, H - 42 * mm, "CBAM EMBEDDED EMISSIONS DECLARATION FOR CUSTOMERS")
    c.setFont("Helvetica", 10)
    rows = [("Customer", BUYER), ("Installation", "Shivalik Integrated Steel Plant, Unit 2 (rolling mill + melt shop)"),
            ("Installation ID (UN/LOCODE-based)", "IN-PB-GOB-0042 (sample)"), ("Production route", "BF-BOF with continuous casting"),
            ("Good / CN code", "Wire rod, CN 7213 91 90"), ("Reporting period", "01-01-2026 to 30-06-2026"),
            ("Specific embedded emissions (direct)", "2.36 tCO2e per tonne"), ("Indirect emissions (for information)", "0.31 tCO2e per tonne"),
            ("Methodology", "Implementing Regulation (EU) 2025/2547; Communication Template v2026"),
            ("Verification", "Verified by TerraVerify Certification Pvt. Ltd. (fictional), accreditation no. NABCB-CBAM-0017 (sample)"),
            ("Verification statement date", "28-08-2026"), ("Carbon price paid in country of origin", "None")]
    y = H - 55 * mm
    for k, v in rows:
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(18 * mm, y, k)
        c.setFont("Helvetica", 9.5)
        c.drawString(85 * mm, y, v)
        y -= 7.5 * mm
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, y - 6 * mm, "The full operator's emissions report and verification statement are available to authorised CBAM declarants")
    c.drawString(18 * mm, y - 11 * mm, "through the CBAM Registry operators portal.")
    c.drawString(140 * mm, 45 * mm, "Head - Sustainability")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(18 * mm, 20 * mm, "Sample document generated for a software demo. Company, installation and verifier are fictional.")
    c.showPage()
    c.save()
    save("11_supplier_emissions_declaration.pdf", buf.getvalue(), "application/pdf", "Supplier reply: verified wire rod emissions (PDF)", {
        "doc_type": "supplier_emissions_declaration", "issuer": "Shivalik Wire & Steel Ltd.", "recipient": BUYER, "document_number": None,
        "document_date": "2026-08-28", "period_start": "2026-01-01", "period_end": "2026-06-30", "languages": ["English"],
        "line_items": [item("supplier_see", "Wire rod specific embedded emissions (direct), BF-BOF", 2.36, "tCO2e per tonne",
                            "Specific embedded emissions (direct) 2.36 tCO2e per tonne", cn_code="7213 91 90",
                            party="Shivalik Wire & Steel Ltd.", verified=True, period_start="2026-01-01", period_end="2026-06-30")],
        "reading_notes": ["Indirect value 0.31 tCO2e/t given for information only."]}, stage="supplier_reply", kind="supplier")


if __name__ == "__main__":
    wire_rod_pdf()
    lpg_photo("02_lpg_invoice_apr.jpg", "SGS/26-27/0107", "09-04-2026", 19800, 64.20, 2, -3.1)
    lpg_photo("03_lpg_invoice_may.jpg", "SGS/26-27/0349", "12-05-2026", 20100, 66.10, 3, 2.4)
    lpg_pdf()
    diesel_photo()
    electricity_pdf()
    register_photo()
    export_pdf("08_export_invoice_de.pdf", "VF/EXP/26-27/019", "18-05-2026", "Kessler Befestigungstechnik GmbH (fictional)", "Germany",
               [("Hex bolts M8-M16, 8.8, zinc plated", "7318 15", 80.0, 38, 124800.0), ("Hex nuts M8-M16, class 8", "7318 16", 25.0, 12, 35500.0)],
               "Export invoice to Germany (PDF)")
    export_photo()
    export_pdf("10_export_invoice_uk.pdf", "VF/EXP/26-27/027", "24-06-2026", "Midlands Fixings Ltd (fictional)", "United Kingdom",
               [("Hex bolts M12-M20, 10.9", "7318 15", 20.0, 10, 34600.0)], "Export invoice to United Kingdom (PDF)")
    supplier_declaration_pdf()
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {len(manifest)} documents")
