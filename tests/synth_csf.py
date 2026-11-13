"""Synthetic Constancias de Situación Fiscal with ground truth, and the ways a phone or a
scanner spoils them (the `csf` lane's corpus).

Every value here is invented and obviously so: RFCs start with `XA`, `XB` or `XC`, the
idCIF is all nines, the names say «SINTÉTICA», «EJEMPLO», «FICTICIA», the postal code is
99999. No real constancia, person, company or address was used or copied, and the
legal boilerplate a real constancia prints is replaced by a paragraph that says it is a
test. The layout follows the public shape of a constancia, which is what the reader is
written against:

- page 1: header images, the cédula block (QR code, RFC, name, idCIF) on the left and
  «Lugar y Fecha de Emisión» on the right, **interleaved line by line** with the left
  block and wrapping so that a line reader sees `social NOVIEMBRE DE 2026`; then the
  identity table (label | value) and the two-column address grid, where a long label
  pushes its value onto the next line;
- page 2: «Y Calle» (the address straddles the page break), actividades económicas,
  regímenes, obligaciones, a boilerplate paragraph and the «Cadena Original Sello»
  (`||AAAA/MM/DD HH:MM:SS|<RFC>|CONSTANCIA DE SITUACIÓN FISCAL|<idCIF>|…||`), wrapped;
- page 3: nearly empty: images and the page number.

`degrade` turns a rendered page into a scan or a photo at a named level; `scanned_pdf`
packs degraded pages back into a PDF with no text layer. Both are seeded: the same call
gives the same pixels."""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import date, datetime

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MONTHS_ES = "ENERO FEBRERO MARZO ABRIL MAYO JUNIO JULIO AGOSTO SEPTIEMBRE OCTUBRE NOVIEMBRE DICIEMBRE".split()
QR_BASE = "https://siat.sat.gob.mx/app/qr/faces/pages/mobile/validadorqr.jsf"


@dataclass
class CsfTruth:
    """What the reader must return, keyed by the contract's field names. An empty
    string is a field the constancia prints blank; a missing key does not apply."""

    kind: str
    legal_form: str
    fields: dict[str, str]
    regimenes: list[tuple[str, str, str]]  # name, start ISO, end ISO or ""
    activities: list[tuple[int, str, int, str, str]]  # order, name, percentage, start, end
    obligations: list[tuple[str, str, str, str]]  # description, due rule, start, end
    seal_timestamp: str  # ISO, as the cadena prints it
    qr_url: str
    pages: int = 3
    sensitive: list[str] = field(default_factory=list)  # strings no log line may carry


def _iso(d: date) -> str:
    return d.isoformat()


def _dmy(d: date) -> str:
    return f"{d:%d/%m/%Y}"


def _spelled(d: date) -> str:
    return f"{d.day:02d} DE {MONTHS_ES[d.month - 1]} DE {d.year}"


ADDRESS = {
    "postalCode": "99999",
    "streetType": "CALLE",
    "streetName": "CALLE INVENTADA DE PRUEBA",
    "exteriorNumber": "123",
    "interiorNumber": "",
    "neighborhood": "COLONIA FICTICIA",
    "locality": "",
    "municipality": "MUNICIPIO EJEMPLO",
    "state": "ESTADO DE PRUEBA",
    "betweenStreet": "CALLE PRIMERA",
    "andStreet": "CALLE SEGUNDA",
}

KINDS = ("pf_resico", "pf_ended", "pm_general")


def truth_for(kind: str, emitted: datetime = datetime(2026, 11, 12, 10, 15, 30)) -> CsfTruth:
    """The three synthetic taxpayers: a persona física in RESICO, a persona física with
    an ended régimen, a persona moral in the régimen general."""
    id_cif = "19999999901" if kind != "pm_general" else "29999999902"
    common = {
        "idCif": id_cif,
        "tradeName": "",
        "padronStatus": "ACTIVO",
        "emissionPlace": f"{ADDRESS['municipality']}, {ADDRESS['state']}",  # printed «X , Y»
        "emissionDate": _iso(emitted.date()),
        **ADDRESS,
    }
    if kind == "pf_resico":
        rfc = "XAXP900101AB1"
        fields = {
            "rfc": rfc,
            "curp": "XAXP900101HDFXXX01",
            "givenNames": "ANA SINTÉTICA",
            "firstSurname": "EJEMPLO",
            "secondSurname": "INVENTADO",
            "operationsStartDate": "2022-01-01",
            "lastStatusChangeDate": "2022-01-01",
            **common,
        }
        regimenes = [("Régimen Simplificado de Confianza", "2022-01-01", "")]
        activities = [(1, "Otros servicios profesionales, científicos y técnicos", 100, "2022-01-01", "")]
        obligations = [
            (
                "Pago definitivo mensual de IVA.",
                "A más tardar el día 17 del mes inmediato posterior al periodo que corresponda.",
                "2022-01-01",
                "",
            ),
            (
                "Declaración anual de ISR del ejercicio. Régimen Simplificado de Confianza.",
                "A más tardar el 30 de abril del ejercicio siguiente.",
                "2022-01-01",
                "",
            ),
        ]
        form = "persona_fisica"
    elif kind == "pf_ended":
        rfc = "XBXQ850315CD2"
        fields = {
            "rfc": rfc,
            "curp": "XBXQ850315MDFXXX02",
            "givenNames": "LUISA EJEMPLAR",
            "firstSurname": "SINTÉTICA",
            "secondSurname": "",
            "operationsStartDate": "2015-03-01",
            "lastStatusChangeDate": "2021-12-31",
            **common,
        }
        regimenes = [
            ("Régimen de Sueldos y Salarios e Ingresos Asimilados a Salarios", "2015-03-01", "2021-12-31"),
            ("Régimen Simplificado de Confianza", "2022-01-01", ""),
        ]
        activities = [
            (1, "Asalariado", 60, "2015-03-01", "2021-12-31"),
            (2, "Otros servicios de apoyo a los negocios", 40, "2022-01-01", ""),
        ]
        obligations = [
            (
                "Pago definitivo mensual de IVA.",
                "A más tardar el día 17 del mes inmediato posterior al periodo que corresponda.",
                "2022-01-01",
                "",
            ),
        ]
        form = "persona_fisica"
    elif kind == "pm_general":
        rfc = "XCX010101EF3"
        fields = {
            "rfc": rfc,
            "legalName": "EMPRESA FICTICIA DE PRUEBAS",
            "regimenCapital": "SOCIEDAD ANONIMA DE CAPITAL VARIABLE",
            "operationsStartDate": "2010-01-01",
            "lastStatusChangeDate": "2010-01-01",
            **common,
            "tradeName": "PRUEBAS FICTICIAS",
        }
        regimenes = [("Régimen General de Ley Personas Morales", "2010-01-01", "")]
        activities = [(1, "Otros servicios relacionados con la contabilidad", 100, "2010-01-01", "")]
        obligations = [
            (
                "Entero de retenciones mensuales de ISR por sueldos y salarios.",
                "A más tardar el día 17 del mes inmediato posterior al periodo que corresponda.",
                "2010-01-01",
                "",
            ),
            (
                "Pago provisional mensual de ISR personas morales régimen general.",
                "A más tardar el día 17 del mes inmediato posterior al periodo que corresponda.",
                "2010-01-01",
                "",
            ),
        ]
        form = "persona_moral"
    else:
        raise ValueError(kind)
    qr_url = f"{QR_BASE}?D1=10&D2=1&D3={id_cif}_{rfc}"
    sensitive = [v for v in fields.values() if len(v) >= 5] + [qr_url, emitted.strftime("%Y/%m/%d %H:%M:%S")]
    return CsfTruth(
        kind=kind,
        legal_form=form,
        fields=fields,
        regimenes=regimenes,
        activities=activities,
        obligations=obligations,
        seal_timestamp=emitted.isoformat(),
        qr_url=qr_url,
        sensitive=sensitive,
    )


def _header_image(width: int, height: int, seed: int) -> ImageReader:
    """A grey pattern that stands for a logo; it carries no text."""
    img = Image.new("RGB", (width, height), (235, 235, 235))
    draw = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for _ in range(12):
        x, y = rnd.randrange(width), rnd.randrange(height)
        r = rnd.randrange(4, max(5, height // 3))
        shade = rnd.randrange(120, 200)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(shade, shade, shade))
    return ImageReader(img)


def _wrap(text: str, width: float, font: str, size: float, c: canvas.Canvas) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if c.stringWidth(trial, font, size) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_csf(
    kind: str = "pf_resico", *, emitted: datetime = datetime(2026, 11, 12, 10, 15, 30)
) -> tuple[bytes, CsfTruth]:
    """A born-digital constancia (text layer, three pages) and its truth."""
    t = truth_for(kind, emitted)
    f = t.fields
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    reg, bold = "Helvetica", "Helvetica-Bold"

    def page_furniture(number: int) -> None:
        c.drawImage(_header_image(300, 60, number), 40, height - 70, width=170, height=34)
        c.drawImage(_header_image(300, 60, number + 10), width - 210, height - 70, width=170, height=34)
        c.drawImage(_header_image(600, 40, number + 20), 40, 30, width=532, height=22)
        c.setFont(reg, 7)
        c.drawRightString(width - 40, 60, f"Página [{number}] de [3]")

    # ── page 1 ──────────────────────────────────────────────────────────────
    page_furniture(1)
    c.setFont(bold, 9)
    c.drawString(40, height - 92, "CÉDULA DE IDENTIFICACIÓN FISCAL")
    qr = QrCodeWidget(t.qr_url)
    x0, y0, x1, y1 = qr.getBounds()
    drawing = Drawing(100, 100, transform=[100 / (x1 - x0), 0, 0, 100 / (y1 - y0), 0, 0])
    drawing.add(qr)
    renderPDF.draw(drawing, c, 40, height - 210)
    name = (
        f["legalName"]
        if t.legal_form == "persona_moral"
        else (f"{f['givenNames']} {f['firstSurname']} {f['secondSurname']}".strip())
    )
    # Left block and right block share baselines, so the text layer interleaves them.
    left = [
        (height - 118, bold, 10, f["rfc"]),
        (height - 130, reg, 7, "Registro Federal de Contribuyentes"),
        (height - 146, bold, 9, name),
        (height - 158, reg, 7, "Nombre, denominación o razón"),
        (height - 170, reg, 7, "social"),
        (height - 186, bold, 8, f"idCIF: {f['idCif']}"),
        (height - 200, reg, 7, "VALIDA TU INFORMACIÓN FISCAL"),
    ]
    for y, font, size, text in left:
        c.setFont(font, size)
        c.drawString(150, y, text)
    c.setFont(bold, 10)
    c.drawString(340, height - 92, "CONSTANCIA DE SITUACIÓN FISCAL")
    c.setFont(reg, 8)
    c.drawString(360, height - 118, "Lugar y Fecha de Emisión")
    spelled = _spelled(emitted.date())
    day_part, rest = spelled.split(" DE ", 1)
    c.drawString(340, height - 146, f"{f['municipality']} , {f['state']} A {day_part} DE")
    c.drawString(340, height - 170, rest)

    y = height - 236
    c.setFont(bold, 9)
    c.drawString(40, y, "Datos de Identificación del Contribuyente:")
    y -= 16
    if t.legal_form == "persona_moral":
        identity = [
            ("RFC:", f["rfc"]),
            ("Denominación/Razón Social:", f["legalName"]),
            ("Régimen Capital:", f["regimenCapital"]),
            ("Nombre Comercial:", f["tradeName"]),
            ("Fecha inicio de operaciones:", _spelled(date.fromisoformat(f["operationsStartDate"]))),
            ("Estatus en el padrón:", f["padronStatus"]),
            ("Fecha de último cambio de estado:", _spelled(date.fromisoformat(f["lastStatusChangeDate"]))),
        ]
    else:
        identity = [
            ("RFC:", f["rfc"]),
            ("CURP:", f["curp"]),
            ("Nombre (s):", f["givenNames"]),
            ("Primer Apellido:", f["firstSurname"]),
            ("Segundo Apellido:", f["secondSurname"]),
            ("Fecha inicio de operaciones:", _spelled(date.fromisoformat(f["operationsStartDate"]))),
            ("Estatus en el padrón:", f["padronStatus"]),
            ("Fecha de último cambio de estado:", _spelled(date.fromisoformat(f["lastStatusChangeDate"]))),
            ("Nombre Comercial:", f["tradeName"]),
        ]
    for label, value in identity:
        c.setFont(reg, 8)
        c.drawString(40, y, label)
        c.setFont(bold, 8)
        c.drawString(210, y, value)
        y -= 14

    y -= 10
    c.setFont(bold, 9)
    c.drawString(40, y, "Datos del domicilio registrado")
    y -= 16
    grid = [
        (("Código Postal:", f["postalCode"]), ("Tipo de Vialidad:", f["streetType"])),
        (("Nombre de Vialidad:", f["streetName"]), ("Número Exterior:", f["exteriorNumber"])),
        (("Número Interior:", f["interiorNumber"]), ("Nombre de la Colonia:", f["neighborhood"])),
        (
            ("Nombre de la Localidad:", f["locality"]),
            ("Nombre del Municipio o Demarcación Territorial:", f["municipality"]),
        ),
        (("Nombre de la Entidad Federativa:", f["state"]), ("Entre Calle:", f["betweenStreet"])),
    ]  # fmt: skip
    for pair in grid:
        wrapped = False
        for x, (label, value) in zip((40, 310), pair, strict=True):
            c.setFont(reg, 8)
            c.drawString(x, y, label)
            label_end = x + c.stringWidth(label, reg, 8) + 4
            c.setFont(bold, 8)
            if label_end + c.stringWidth(value, bold, 8) > x + 262:  # no room: the value wraps under its label
                c.drawString(x, y - 12, value)
                wrapped = True
            else:
                c.drawString(label_end, y, value)
        y -= 26 if wrapped else 16
    c.showPage()

    # ── page 2 ──────────────────────────────────────────────────────────────
    page_furniture(2)
    y = height - 96
    c.setFont(reg, 8)
    c.drawString(40, y, "Y Calle:")
    c.setFont(bold, 8)
    c.drawString(40 + c.stringWidth("Y Calle:", reg, 8) + 4, y, f["andStreet"])

    def table(title: str, headers: list[tuple[float, str]], rows: list[list[tuple[float, float, str]]]) -> None:
        nonlocal y
        y -= 24
        c.setFont(bold, 9)
        c.drawString(40, y, title)
        y -= 14
        c.setFont(bold, 7)
        for x, text in headers:
            c.drawString(x, y, text)
        y -= 12
        c.setFont(reg, 7)
        for row in rows:
            used = 1
            for x, cell_width, text in row:
                lines = _wrap(text, cell_width, reg, 7, c)
                for n, line in enumerate(lines):
                    c.drawString(x, y - 10 * n, line)
                used = max(used, len(lines))
            y -= 10 * used + 4

    def dmy(iso: str) -> str:
        return _dmy(date.fromisoformat(iso)) if iso else ""

    table(
        "Actividades Económicas:",
        [(40, "Orden"), (80, "Actividad Económica"), (330, "Porcentaje"), (400, "Fecha Inicio"), (480, "Fecha Fin")],
        [
            [(40, 30, str(o)), (80, 240, n), (330, 60, str(p)), (400, 70, dmy(s)), (480, 70, dmy(e))]
            for o, n, p, s, e in t.activities
        ],
    )
    table(
        "Regímenes:",
        [(40, "Régimen"), (400, "Fecha Inicio"), (480, "Fecha Fin")],
        [[(40, 340, n), (400, 70, dmy(s)), (480, 70, dmy(e))] for n, s, e in t.regimenes],
    )
    table(
        "Obligaciones:",
        [
            (40, "Descripción de la Obligación"),
            (230, "Descripción Vencimiento"),
            (400, "Fecha Inicio"),
            (480, "Fecha Fin"),
        ],
        [
            [(40, 180, d), (230, 160, v), (400, 70, dmy(s)), (480, 70, dmy(e))]
            for d, v, s, e in t.obligations
        ],
    )  # fmt: skip
    y -= 20
    c.setFont(reg, 7)
    for line in _wrap(
        "Este documento es una muestra sintética para probar un lector de documentos: ninguna persona, "
        "empresa, domicilio o número que aparece en él existe, y este párrafo ocupa el lugar del texto "
        "legal que una constancia real imprime.",
        530,
        reg,
        7,
        c,
    ):
        c.drawString(40, y, line)
        y -= 10
    y -= 12
    c.setFont(bold, 7)
    c.drawString(40, y, "Cadena Original Sello:")
    y -= 10
    stamp = emitted.strftime("%Y/%m/%d %H:%M:%S")
    rnd = random.Random(f"{kind}-seal")
    tail = "".join(rnd.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(40))
    cadena = f"||{stamp}|{f['rfc']}|CONSTANCIA DE SITUACIÓN FISCAL|{f['idCif']}|{tail}||"
    c.setFont(reg, 7)
    for line in _wrap(cadena, 530, reg, 7, c):
        c.drawString(40, y, line)
        y -= 10
    y -= 8
    c.setFont(bold, 7)
    c.drawString(40, y, "Sello Digital:")
    y -= 10
    seal = "".join(rnd.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/") for _ in range(344))
    c.setFont(reg, 6)
    for start in range(0, len(seal), 110):
        c.drawString(40, y, seal[start : start + 110])
        y -= 9
    c.showPage()

    # ── page 3: nearly empty ───────────────────────────────────────────────
    page_furniture(3)
    c.drawImage(_header_image(400, 300, 33), 156, 300, width=300, height=220)
    c.showPage()
    c.save()
    return buf.getvalue(), t


# ── scans and photos ────────────────────────────────────────────────────────

# Named degradation levels: what a person's upload may look like. dpi is the
# resolution the page reaches the reader at; the rest is applied in this order.
LEVELS: dict[str, dict] = {
    "clean": {"dpi": 300},
    "scan": {"dpi": 200, "rotate": 0.8, "noise": 8, "jpeg": 85},
    "photo": {"dpi": 170, "rotate": -2.5, "blur": 0.7, "noise": 14, "light": 0.25, "jpeg": 65},
    "poor_photo": {"dpi": 120, "rotate": 4.0, "blur": 1.1, "noise": 22, "light": 0.35, "jpeg": 40, "crop": 0.06},
    "upside_down": {"dpi": 200, "rotate": 180.6, "noise": 8, "jpeg": 80},
}


def render_page(pdf: bytes, page: int, dpi: int) -> Image.Image:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        return doc[page - 1].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        doc.close()


def degrade(image: Image.Image, level: str, seed: int = 0) -> Image.Image:
    """The page as a scanner or a phone would hand it over at `level`."""
    spec = LEVELS[level]
    rnd = random.Random(f"{level}-{seed}")
    img = image.convert("RGB")
    if spec.get("rotate"):
        img = img.rotate(spec["rotate"], resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(250, 250, 248))
    if spec.get("crop"):
        w, h = img.size
        img = img.crop((0, 0, int(w * (1 - spec["crop"])), h))  # the right edge did not make it into the frame
    if spec.get("light"):  # uneven light: darker towards one corner
        w, h = img.size
        gradient = Image.linear_gradient("L").resize((w, h)).rotate(rnd.choice((0, 90, 180, 270)))
        shade = ImageEnhance.Brightness(gradient).enhance(spec["light"])
        img = ImageChops.subtract(img, Image.merge("RGB", (shade, shade, shade)))
    if spec.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(spec["blur"]))
    if spec.get("noise"):
        # Seeded (Pillow's effect_noise is not): uniform noise with the level's spread.
        spread = spec["noise"] / 73.6  # the standard deviation of uniform 0–255
        raw = Image.frombytes("L", img.size, rnd.randbytes(img.width * img.height))
        noise = raw.point(lambda v: max(0, min(255, round(128 + (v - 128) * spread))))
        img = ImageChops.add(img, Image.merge("RGB", (noise, noise, noise)), scale=1.0, offset=-128)
    if spec.get("jpeg"):
        out = io.BytesIO()
        img.save(out, "JPEG", quality=spec["jpeg"])
        img = Image.open(io.BytesIO(out.getvalue())).convert("RGB")
    return img


def page_image(pdf: bytes, page: int, level: str, seed: int = 0, fmt: str = "JPEG") -> bytes:
    """One page of a constancia as an uploaded image (a photo of page 1, typically)."""
    img = degrade(render_page(pdf, page, LEVELS[level]["dpi"]), level, seed)
    out = io.BytesIO()
    img.save(out, fmt, **({"quality": LEVELS[level].get("jpeg", 92)} if fmt == "JPEG" else {}))
    return out.getvalue()


def scanned_pdf(pdf: bytes, level: str, seed: int = 0, pages: int = 3) -> bytes:
    """The constancia as a PDF of degraded page images: no text layer at all."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    for number in range(1, pages + 1):
        img = degrade(render_page(pdf, number, LEVELS[level]["dpi"]), level, seed + number)
        c.drawImage(ImageReader(img), 0, 0, width=width, height=height)
        c.showPage()
    c.save()
    return buf.getvalue()
