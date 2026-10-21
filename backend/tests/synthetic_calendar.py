"""Generate original PDF grids in memory; no institution's documents are bundled.

The invented appointments exercise the supported grid grammar, time conversion,
exam group filtering and source preservation. This is parser regression data,
not an assertion about any real university timetable.
"""
import pymupdf

CLASS_COUNT = 4
EXAM_COUNT = 2
TOTAL_COUNT = CLASS_COUNT + EXAM_COUNT


def _page(document, heading, *, exams=False, month_year=''):
    page = document.new_page(width=640, height=760)
    if not exams:
        page.insert_text((70, 35), '2026-2027 QUART CURS GRUP EC', fontsize=10)
    page.insert_text((70, 70), heading, fontsize=10)
    if month_year:
        page.insert_text((70, 95), month_year, fontsize=8)
    # Hour labels are centred in each one-hour row, as in a weekly grid.
    font = pymupdf.Font('helv')
    baseline_offset = (font.ascender + font.descender) * 8 / 2
    for hour in range(7, 21):
        centre = -140 + (hour + .5) * 40
        page.insert_text((30, centre + baseline_offset), str(hour), fontsize=8)
    days = ['lu.', 'ma.', 'mi.', 'ju.', 'vi.']
    centres = [120, 230, 340, 450, 560] if exams else [105, 175, 245, 315, 385, 455, 525]
    if not exams:
        days += ['sá.', 'do.']
    for day, centre in zip(days, centres):
        width = pymupdf.get_text_length(day, fontname='helv', fontsize=8)
        page.insert_text((centre - width / 2, 135), day, fontsize=8)
    return page, centres


def _event(page, centres, day, start, end, label, location='', *, exams=False):
    half_width = 49 if exams else 30
    rect = pymupdf.Rect(centres[day] - half_width, -140 + start * 40,
                        centres[day] + half_width, -140 + end * 40)
    page.draw_rect(rect, color=None, fill=(.2, .65, .9))
    page.insert_text((rect.x0 + 3, rect.y0 + 11), label, fontsize=6)
    if location:
        page.insert_text((rect.x0 + 3, rect.y0 + 23), location, fontsize=6)


def synthetic_timetable():
    """Four invented classes covering different weekdays and DST periods."""
    with pymupdf.open() as document:
        page, centres = _page(document, '14 septiembre - 20 septiembre')
        _event(page, centres, 0, 8.5, 10.5, 'BQ', '(Aula Demo 1)')
        _event(page, centres, 2, 14, 16, 'QA3', '(Aula Demo 2)')
        page, centres = _page(document, '2 noviembre - 8 noviembre')
        _event(page, centres, 0, 9, 10, 'TDME', '(Aula Demo 3)')
        page, centres = _page(document, '12 abril - 18 abril')
        _event(page, centres, 0, 10.25, 12.25, 'QO3', '(Aula Demo 4)')
        return document.tobytes(no_new_id=True)


def synthetic_exams():
    """Two matching exams plus deliberately excluded group B/EV entries."""
    with pymupdf.open() as document:
        page, centres = _page(document, '26 octubre - 1 noviembre', exams=True, month_year='octubre 2026')
        _event(page, centres, 3, 15, 17, 'Lab. Q. Orgànica II EC', exams=True)
        _event(page, centres, 0, 9, 11, 'Bioquímica B EC', exams=True)
        _event(page, centres, 1, 9, 11, 'Química Orgànica III EV', exams=True)
        page, centres = _page(document, '11 enero - 17 enero', exams=True, month_year='enero 2027')
        _event(page, centres, 1, 9, 11, 'Bioquímica EC', exams=True)
        return document.tobytes(no_new_id=True)
