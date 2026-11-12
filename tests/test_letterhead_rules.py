"""The letterhead rules as pure functions over synthetic lines and boxes.

Every name here is invented. A page is 612 x 792 points: the top band ends at 198, the
bottom band starts at 514.8 (see the constants in tlacuilo/parsers/letterhead.py)."""

import json
from datetime import date

import pytest

from tlacuilo.contract import Issuer
from tlacuilo.engines.text_layer import Line, Page, Word
from tlacuilo.parsers import letterhead as lh


def line(text: str, top: float, *, x: float = 60.0, size: float = 10.0, page: int = 1, conf=None) -> Line:
    """Words laid left to right at `size` points; a chunk after «   » (three spaces)
    starts 150 points further right, as a second column would."""
    words, cursor = [], x
    for chunk in text.split("   "):
        for token in chunk.split():
            width = len(token) * size * 0.5
            words.append(Word(token, cursor, cursor + width, top, top + size, conf))
            cursor += width + size * 0.28
        cursor += 150
    return Line(page, words)


def page(*lines: Line, number: int = 1) -> Page:
    return Page(number=number, width=612.0, height=792.0, lines=list(lines))


def seg(text: str, **kw) -> lh.Segment:
    (only,) = lh.segments(line(text, 50, **kw), 0)
    return only


# --- bands and segments -----------------------------------------------------------


def test_bands_split_a_full_page_into_letterhead_first_body_lines_and_signature_block():
    lines = [line(f"renglón {i}", top) for i, top in enumerate(range(40, 760, 20))]  # 36 lines
    b = lh.bands(page(*lines))
    assert b.top == list(range(8))  # tops 40..180 are above 198
    assert b.body_head == list(range(8, 16))
    assert b.bottom == list(range(24, 36))  # top >= 514.8, which here is also the last 12


def test_a_short_letter_signs_mid_page_and_its_last_lines_are_still_the_bottom_band():
    lines = [line(f"renglón {i}", top) for i, top in enumerate(range(40, 400, 20))]  # 18 lines, none low
    b = lh.bands(page(*lines))
    assert b.bottom == list(range(8, 18)) and not set(b.top) & set(b.bottom)


def test_segments_cut_a_line_at_marks_and_at_wide_gaps():
    parts = lh.segments(line("Clínica Inventada · Calle Falsa 1   Tel. 55 0000 0000", 50), 0)
    assert [p.text for p in parts] == ["Clínica Inventada", "Calle Falsa 1", "Tel. 55 0000 0000"]


# --- institution ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Escuela Primaria Ejemplo Sintético",
        "COLEGIO FICTICIO DE PRUEBAS",
        "Jardín de Niños Ejemplo Inventado",
        "JARDIN DE NINOS EJEMPLO INVENTADO",  # OCR without accents
        "Instituto Ficticio de Pruebas",
        "Centro de Atención Ficticia No. 4",
        "Clínica Inventada del Sur",
        "Hospital Ficticio de Pruebas",
        "Consultorio de Pruebas Sintéticas",
        "Universidad Ficticia de Ejemplo",
        "CAM Ejemplo 15",
        "USAER Ejemplo 42",
        "Secundaria Ficticia 120",
        "Grupo Educativo Ejemplo, S.C.",
        "Servicios Inventados de Prueba A.C.",
    ],
)
def test_an_organisation_line_is_an_institution_as_written(text):
    assert lh.institution_name(seg(text)) == (text, len(text.split()))


@pytest.mark.parametrize(
    "text",
    [
        "Av. Universidad 100, Col. Ejemplo",  # an address that names a keyword
        "Calle Hospital 5",
        "Centro, Villa Ejemplo, Edo.",  # a place, not an organisation
        "Tel. 55 0000 0000",
        "contacto@colegio.invalid",
        "www.colegio-ejemplo.invalid",
        "Dra. Ana Ejemplo Inventado",  # a person
        "A quien corresponda:",
        "cam ejemplo 15",  # the acronym counts only in capitals
        "Constancia de estudios",
        "Hospital",  # a keyword alone names nothing
    ],
)
def test_addresses_contacts_people_and_plain_text_are_not_institutions(text):
    assert lh.institution_name(seg(text)) is None


def test_an_institution_name_ends_before_a_code_a_licence_or_a_run_of_numbers():
    assert lh.institution_name(seg("Escuela Primaria Ejemplo C.C.T. 00XXX0000X"))[0] == "Escuela Primaria Ejemplo"
    licensed = lh.institution_name(seg("Consultorio Ejemplo Inventado Cédula 99999999"))
    assert licensed[0] == "Consultorio Ejemplo Inventado"
    assert lh.institution_name(seg("Clínica Inventada del Sur 99999999"))[0] == "Clínica Inventada del Sur"
    assert lh.institution_name(seg("Clínica Inventada 99 999 999"))[0] == "Clínica Inventada"


def test_the_first_institution_wins_unless_another_is_set_in_clearly_larger_type():
    same = page(line("Instituto Ficticio de Pruebas", 40), line("Clínica Inventada del Sur", 60))
    found = lh.find_institution([same])
    assert (found.value, found.confidence) == ("Instituto Ficticio de Pruebas", 0.75)  # two candidates: .90 - .15
    larger = page(line("Instituto Ficticio de Pruebas", 40), line("Clínica Inventada del Sur", 60, size=16))
    assert lh.find_institution([larger]).value == "Clínica Inventada del Sur"


def test_institution_is_read_in_the_top_band_only_and_page_two_scores_lower():
    body_only = page(line("membrete sin organización", 40), line("Escuela Primaria Ejemplo Sintético", 400))
    assert lh.find_institution([body_only]) is None
    second = page(line("Escuela Primaria Ejemplo Sintético", 40, page=2), number=2)
    found = lh.find_institution([body_only, second])
    assert (found.page, found.confidence) == (2, 0.75)
    first = lh.find_institution([page(line("Escuela Primaria Ejemplo Sintético", 40))])
    assert (first.page, first.confidence, len(first.bbox)) == (1, 0.90, 4)


# --- professional -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Dra. Ana Ejemplo Inventado", "Dra. Ana Ejemplo Inventado"),
        ("Dr. Luis de la Prueba Sintética", "Dr. Luis de la Prueba Sintética"),
        ("LIC. MARÍA EJEMPLO FICTICIO", "LIC. MARÍA EJEMPLO FICTICIO"),
        ("Mtra. Luz Muestra Inventada", "Mtra. Luz Muestra Inventada"),
        ("Mtro. J. Ejemplo Ficticio", "Mtro. J. Ejemplo Ficticio"),
        ("Psic. Ana Ejemplo Inventado", "Psic. Ana Ejemplo Inventado"),
        ("T.F. Rosa Prueba Inventada", "T.F. Rosa Prueba Inventada"),
        ("T. L. Rosa Prueba Inventada", "T. L. Rosa Prueba Inventada"),
        ("Doctora Ana Ejemplo Inventado", "Doctora Ana Ejemplo Inventado"),
        ("Psicólogo Mario Prueba Sintética", "Psicólogo Mario Prueba Sintética"),
        ("Maestra Luz Muestra Inventada", "Maestra Luz Muestra Inventada"),
        ("Neuropsicóloga Ana Ejemplo Inventado", "Neuropsicóloga Ana Ejemplo Inventado"),
        ("Elaboró: Psic. Ana Ejemplo Inventado", "Psic. Ana Ejemplo Inventado"),  # after a label
        # The name ends where the name ends: a role, a licence cue, a number, a comma.
        ("Dra. Ana Ejemplo Inventado Directora General", "Dra. Ana Ejemplo Inventado"),
        ("DRA. ANA EJEMPLO INVENTADO NEUROPSICÓLOGA CLÍNICA", "DRA. ANA EJEMPLO INVENTADO"),
        ("Dra. Ana Ejemplo Inventado, Céd. Prof. 99999999", "Dra. Ana Ejemplo Inventado"),
        ("Dra. Ana Ejemplo Inventado Céd. Prof. 99999999", "Dra. Ana Ejemplo Inventado"),
        ("Dra. Ana Ejemplo Inventado Cédula 99999999", "Dra. Ana Ejemplo Inventado"),
        ("Dra. Ana Ejemplo Inventado 99999999", "Dra. Ana Ejemplo Inventado"),
        ("Dra. Ana Ejemplo Inventado (responsable)", "Dra. Ana Ejemplo Inventado"),
        ("Psic. Ana Ejemplo Inventado quien suscribe", "Psic. Ana Ejemplo Inventado"),
    ],
)
def test_a_titled_name_is_returned_as_written_and_nothing_after_it(text, name):
    got, used = lh.person_name(seg(text))
    assert got == name
    assert " ".join(w.text for w in used).rstrip(",") == name


@pytest.mark.parametrize(
    "text",
    [
        "Ana Ejemplo Inventado",  # no title: the rules do not guess who is a professional
        "Dra. Ana",  # one word is not a name
        "Dr.",
        "Lic. en Psicología Educativa",
        "Maestra de grupo: tercero",
        "To Ana Ejemplo Inventado",  # «TO» is a title only in capitals or with points
        "Escuela Primaria Profr. Mario Prueba Sintética",  # a school named after a person
        "Dra. 99999999",
    ],
)
def test_lines_without_a_title_and_a_name_name_no_professional(text):
    assert lh.person_name(seg(text)) is None


def test_a_name_never_carries_a_digit():
    numbered = ("Dra. Ana Ejemplo Inventado 99999999", "Dra. Ana Ejemplo2 Inventado Prueba", "Dr. Luis Prueba 99 999")
    for text in numbered:
        hit = lh.person_name(seg(text))
        assert hit is None or not any(c.isdigit() for c in hit[0])


def signed(*rows: str, start: float = 600.0) -> list[Line]:
    return [line(text, start + 16 * i) for i, text in enumerate(rows)]


def test_the_signature_block_names_the_professional():
    p = page(
        line("Colegio Ficticio de Pruebas", 40),
        *signed("Atentamente", "______________________", "Psic. Ana Ejemplo Inventado", "Psicóloga", "Cédula 99999999"),
    )
    found = lh.find_professional([p])
    assert (found.value, found.confidence, found.page) == ("Psic. Ana Ejemplo Inventado", 0.90, 1)


def test_spaced_capitals_are_still_a_signature_cue():
    p = page(line("membrete", 40), *signed("A T E N T A M E N T E", "Dra. Ana Ejemplo Inventado"))
    assert lh.find_professional([p]).value == "Dra. Ana Ejemplo Inventado"


def test_a_titled_name_far_from_any_signature_cue_is_not_a_signer():
    p = page(line("membrete", 40), *signed("Se menciona a la Dra. Ana Ejemplo Inventado", "Dra. Ana Ejemplo Inventado"))
    assert lh.find_professional([p]) is None


def test_letterhead_alone_scores_lower_and_agreement_with_the_signature_scores_highest():
    head = line("Dra. Ana Ejemplo Inventado", 60)
    only_head = lh.find_professional([page(line("Consultorio de Pruebas Sintéticas", 40), head)])
    assert (only_head.value, only_head.confidence) == ("Dra. Ana Ejemplo Inventado", 0.75)
    both = lh.find_professional([page(head, *signed("Atentamente", "Dra. Ana Ejemplo Inventado"))])
    assert (both.confidence, both.bbox[1]) == (0.95, 616.0)  # the signature's box, not the letterhead's
    differs = lh.find_professional([page(head, *signed("Atentamente", "Psic. Mario Prueba Sintética"))])
    assert (differs.value, differs.confidence) == ("Psic. Mario Prueba Sintética", 0.90)


def test_two_signers_side_by_side_are_two_names_and_the_first_is_returned_with_a_penalty():
    p = page(line("membrete", 40), *signed("Atentamente", "Psic. Ana Ejemplo Inventado   Dra. Luz Muestra Inventada"))
    found = lh.find_professional([p])
    assert (found.value, found.confidence) == ("Psic. Ana Ejemplo Inventado", 0.75)


def test_the_signature_may_be_on_the_second_page():
    one = page(line("Colegio Ficticio de Pruebas", 40), line("cuerpo del texto", 300))
    two = page(*[ln for ln in signed("Atentamente", "Mtra. Luz Muestra Inventada")], number=2)
    for ln in two.lines:
        ln.page = 2
    found = lh.find_professional([one, two])
    assert (found.value, found.page) == ("Mtra. Luz Muestra Inventada", 2)


# --- date -------------------------------------------------------------------------


def test_the_date_under_the_letterhead_is_the_document_date():
    p = page(line("Colegio Ficticio de Pruebas", 40), line("Ciudad de México, a 12 de noviembre de 2026", 130, x=300))
    found = lh.find_document_date([p])
    assert (found.value, found.confidence, found.page) == (date(2026, 11, 12), 0.90, 1)
    assert found.bbox[0] > 300  # the box is the date's, not the whole line's


def test_a_birth_date_is_never_the_document_date():
    cues = (
        "Fecha de nacimiento: 03/05/2019",
        "F.N. 03/05/2019",
        "F. N.: 3 de mayo de 2019",
        "Nació el 3 de mayo de 2019",
    )
    for cue in cues:
        assert lh.find_document_date([page(line(cue, 130))]) is None, cue
    under_label = page(line("Fecha de nacimiento", 130), line("03/05/2019", 146))
    assert lh.find_document_date([under_label]) is None
    with_issue = page(line("Fecha de nacimiento: 03/05/2019", 130), line("Fecha: 12/11/2026", 220))
    assert lh.find_document_date([with_issue]).value == date(2026, 11, 12)


def test_validity_revision_and_address_lines_carry_no_document_date():
    not_issue = (
        "Vigencia: 31/12/2027",
        "Rev. 01/01/2020",
        "Próxima cita: 20/11/2026",
        "Periodo del 01/09/2026 al 30/11/2026",
        "Calle 5 de Mayo 2026, Col. Ejemplo",
    )
    for text in not_issue:
        assert lh.find_document_date([page(line(text, 130))]) is None, text
    assert lh.find_document_date([page(line("Ciclo escolar noviembre de 2026", 130))]) is None  # no day, no date


def test_date_places_in_order_top_band_first_body_lines_then_the_closing_formula():
    top = line("12 de noviembre de 2026", 130)
    head = line("Fecha de evaluación 10 de noviembre de 2026", 220)
    body = [line(f"cuerpo {i}", 236 + 12 * i) for i in range(22)]  # down to 488
    deep = line("valorado el 1 de octubre de 2026 por primera vez", 500)  # body: above the bottom band
    closing = line("Se extiende a los 11 días del mes de noviembre de 2026", 600)
    after = [line(f"firma {i}", 616 + 12 * i) for i in range(11)]  # the closing line and these are the last 12
    assert lh.find_document_date([page(top, head, *body, deep, closing, *after)]).confidence == 0.90
    found = lh.find_document_date([page(line("membrete", 40), head, *body, deep, closing, *after)])
    assert (found.value, found.confidence) == (date(2026, 11, 10), 0.80)
    found = lh.find_document_date([page(line("membrete", 40), *body, deep, closing, *after)])
    assert (found.value, found.confidence) == (date(2026, 11, 11), 0.70)
    # The body itself is not read: a date deep in the text is never returned.
    last = [*after, line("firma 11", 748)]  # twelve lines under it: `deep` is in no band
    assert lh.find_document_date([page(line("membrete", 40), *body, deep, *last)]) is None


def test_an_issue_cue_wins_inside_a_place_and_doubt_lowers_the_confidence():
    p = page(
        line("membrete", 40),
        line("Fecha de evaluación 10 de noviembre de 2026", 220),
        line("Fecha de informe: 12 de noviembre de 2026", 236),
    )
    found = lh.find_document_date([p])
    assert (found.value, found.confidence) == (date(2026, 11, 12), 0.65)  # .80 - .15, two dates in the place
    numeric = lh.find_document_date([page(line("Fecha: 05/11/2026", 130))])
    assert (numeric.value, numeric.confidence) == (date(2026, 11, 5), 0.80)  # .90 - .10, day/month order
    clear = lh.find_document_date([page(line("Fecha: 25/11/2026", 130))])
    assert clear.confidence == 0.90


# --- the whole draft --------------------------------------------------------------


def letter_pages(conf=None) -> list[Page]:
    return [
        page(
            line("Colegio Ficticio de Pruebas, A.C.", 40, size=16, conf=conf),
            line("Calle Inventada 123, Col. Ejemplo, C.P. 00000", 62, conf=conf),
            line("Ciudad de México, a 12 de noviembre de 2026", 130, x=300, conf=conf),
            line("Fecha de nacimiento: 03/05/2019", 220, conf=conf),
            line("Texto de relleno que no describe a nadie.", 240, conf=conf),
            *[
                Line(1, [Word(w.text, w.x0, w.x1, w.top, w.bottom, conf) for w in ln.words])
                for ln in signed("Atentamente", "Psic. Ana Ejemplo Inventado", "Cédula profesional 99999999")
            ],
        )
    ]


def test_the_draft_becomes_a_contract_block_without_the_licence_the_birth_date_or_the_address():
    draft = lh.extract_issuer(letter_pages())
    assert draft.warnings == []
    issuer = Issuer(**lh.to_contract(draft))
    assert issuer.institution.name == "Colegio Ficticio de Pruebas, A.C."
    assert issuer.professional.name == "Psic. Ana Ejemplo Inventado"
    assert issuer.documentDate.date == date(2026, 11, 12)
    text = json.dumps(issuer.model_dump(mode="json"), ensure_ascii=False)
    for leak in ("99999999", "2019", "Inventada 123", "00000", "Cédula"):
        assert leak not in text


def test_ocr_words_lower_every_confidence_and_weak_ocr_lowers_it_more():
    plain = lh.extract_issuer(letter_pages())
    good = lh.extract_issuer(letter_pages(conf=0.93), ocr=True)
    weak = lh.extract_issuer(letter_pages(conf=0.41), ocr=True)
    for name in ("institution", "professional", "document_date"):
        a, b, c = (getattr(d, name) for d in (plain, good, weak))
        assert a.value == b.value == c.value
        assert b.confidence == round(a.confidence * 0.8, 2) and c.confidence == round(a.confidence * 0.6, 2)


def test_nothing_found_is_three_nulls_and_warnings_never_a_guess():
    draft = lh.extract_issuer([page(line("Texto sin membrete ni firma", 300), line("Otra línea del cuerpo", 320))])
    assert (draft.institution, draft.professional, draft.document_date) == (None, None, None)
    assert draft.warnings == [
        "institution not found",
        "professional not found",
        "document date not found",
        "no issuer field recognised",
    ]
    empty = {"institution": None, "professional": None, "documentDate": None}
    assert Issuer(**lh.to_contract(draft)).model_dump() == empty
    assert lh.extract_issuer([]).warnings[-1] == "no issuer field recognised"


def test_only_the_first_two_pages_are_read():
    blank = page(line("cuerpo", 300))
    third = page(line("Colegio Ficticio de Pruebas", 40, page=3), number=3)
    assert lh.extract_issuer([blank, blank, third]).institution is None
