"""Standalone JSON worker for CPU OCR, transcription and PDF operations."""
import base64
from contextlib import redirect_stdout
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from importlib.metadata import version
from xml.sax.saxutils import escape


def ocr_image(image):
    from rapidocr_onnxruntime import RapidOCR
    result, _ = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1)(image)
    return [{'order': index, 'text': item[1], 'confidence': float(item[2])}
            for index, item in enumerate(result or [], 1)]


def read_pdf(payload):
    import pymupdf
    pages = []
    with pymupdf.open(payload['path']) as document:
        if document.needs_pass:
            raise ValueError("PDF已加密，请先解密")
        if len(document) > payload['max_pages']:
            raise ValueError("PDF页数超过处理上限")
        for number, page in enumerate(document, 1):
            text = page.get_text(sort=True)
            chars = [char for char in text if not char.isspace()]
            ratio = chars.count('\ufffd') / len(chars) if chars else 0
            method, confidence = 'text', None
            if len(chars) < payload['min_text_chars'] or ratio > payload['max_replacement_ratio']:
                scale = min(2, (12_000_000 / max(1, page.rect.width * page.rect.height)) ** .5)
                image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                blocks = ocr_image(image.tobytes('png'))
                text = '\n'.join(block['text'] for block in blocks)
                confidence = min((block['confidence'] for block in blocks), default=None)
                method = 'ocr'
            pages.append({'page_number': number, 'text': text, 'method': method, 'confidence': confidence})
    return {'pages': pages}


def transcribe(payload, model_path):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_path, device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
    segments, info = model.transcribe(payload['path'], language=payload.get('language'),
                                      beam_size=5, vad_filter=True)
    result = []
    for segment in segments:
        if segment.end > segment.start:
            result.append({'segment_number': len(result) + 1, 'start_seconds': segment.start,
                           'end_seconds': segment.end, 'text': segment.text})
    return {'segments': result, 'language': info.language, 'duration': info.duration}


def export_pdf(payload):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    font_options = [payload.get('font_path'), 'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/simsun.ttc',
                    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
    font_path = next((str(path) for path in font_options if path and Path(path).is_file()), None)
    if font_path is None:
        raise ValueError("PDF导出需要可用的中文TrueType字体")
    pdfmetrics.registerFont(TTFont('KnowledgeChinese', font_path))
    body = ParagraphStyle('Body', fontName='KnowledgeChinese', fontSize=10, leading=16,
                          spaceAfter=7, wordWrap='CJK', alignment=TA_LEFT)
    heading = ParagraphStyle('Heading', parent=body, fontSize=14, leading=22, spaceBefore=10, keepWithNext=True)
    story, table_rows = [], []
    in_code = False

    def flush_table():
        if table_rows:
            width = max(len(row) for row in table_rows)
            rows = [[Paragraph(escape(cell), body) for cell in row + [''] * (width - len(row))] for row in table_rows]
            table = Table(rows, colWidths=[(A4[0] - 96) / width] * width, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .5, colors.lightgrey),
                                       ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
                                       ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                       ('LEFTPADDING', (0, 0), (-1, -1), 6),
                                       ('TOPPADDING', (0, 0), (-1, -1), 5),
                                       ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
            story.extend([table, Spacer(1, 8)])
            table_rows.clear()

    for line in payload['content'].splitlines():
        if line.strip().startswith('```'):
            flush_table()
            in_code = not in_code
            continue
        if in_code:
            story.append(Paragraph(escape(line).replace(' ', '&#160;') or '&#160;', body))
            continue
        if line.strip().startswith('|') and line.strip().endswith('|'):
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                table_rows.append(cells)
            continue
        flush_table()
        if not line.strip():
            story.append(Spacer(1, 4))
            continue
        style = heading if re.match(r'^#{1,6}\s', line) else body
        line = re.sub(r'^#{1,6}\s+', '', line)
        text = escape(line)
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        story.append(Paragraph(text, style))
    flush_table()
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=48, rightMargin=48,
                                 topMargin=48, bottomMargin=48, title=payload['title'],
                                 subject=payload['provenance'], author='Personal Knowledge Base', invariant=1)

    def footer(canvas, doc):
        canvas.setFont('KnowledgeChinese', 9)
        canvas.drawRightString(A4[0] - 48, 26, str(doc.page))

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    data = output.getvalue()
    import pymupdf
    with pymupdf.open(stream=data, filetype='pdf') as check:
        if not len(check):
            raise ValueError("生成的PDF没有页面")
    return {'pdf_base64': base64.b64encode(data).decode('ascii')}


def main():
    try:
        request = json.load(sys.stdin)
        sys.path.insert(0, request['site_packages'])
        payload = request['payload']
        with redirect_stdout(sys.stderr):
            action = request['action']
            if action == 'ocr':
                result = {'blocks': ocr_image(payload['path'])}
            elif action == 'pdf':
                result = read_pdf(payload)
            elif action == 'transcribe':
                result = transcribe(payload, request['asr_model'])
            elif action == 'export_pdf':
                result = export_pdf(payload)
            else:
                raise ValueError("未知媒体操作")
            distributions = {'ocr': ('rapidocr-onnxruntime',), 'pdf': ('PyMuPDF', 'rapidocr-onnxruntime'),
                             'transcribe': ('faster-whisper', 'av'), 'export_pdf': ('reportlab', 'PyMuPDF')}
            result['engine_versions'] = {name: version(name) for name in distributions[action]}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({'error': f'{type(error).__name__} {error}'}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
