"""
og_images.py — Dynamic Open Graph image generation for social sharing.
Generates 1200x630 PNG preview cards for reports, outlets, and debates.
Uses Pillow with DejaVu system fonts.
"""

from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os

# Fonts
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except:
        return ImageFont.load_default()

FONT_BOLD = lambda s: _font("DejaVuSans-Bold.ttf", s)
FONT_REG = lambda s: _font("DejaVuSans.ttf", s)
FONT_MONO = lambda s: _font("DejaVuSansMono.ttf", s)
FONT_SERIF = lambda s: _font("DejaVuSerif-Bold.ttf", s)

# Colors
BG = (10, 10, 10)
SURFACE = (19, 18, 24)
VIOLET = (168, 85, 247)
PINK = (236, 72, 153)
WHITE = (255, 255, 255)
TEXT2 = (156, 163, 175)
TEXT3 = (107, 114, 128)
GREEN = (34, 197, 94)
AMBER = (245, 158, 11)
RED = (239, 68, 68)

VERDICT_COLORS = {
    'supported': GREEN, 'corroborated': (20, 184, 166), 'plausible': (134, 239, 172),
    'overstated': AMBER, 'disputed': (249, 115, 22), 'not_supported': RED,
    'not_verifiable': TEXT3, 'opinion': TEXT3,
}

def _score_color(score):
    if score is None: return TEXT3
    if score >= 80: return GREEN
    if score >= 60: return (134, 239, 172)
    if score >= 40: return AMBER
    return RED

def _draw_score_ring(draw, cx, cy, radius, score, stroke=6):
    """Draw a score ring arc."""
    color = _score_color(score)
    # Track
    draw.arc([cx-radius, cy-radius, cx+radius, cy+radius], 0, 360, fill=(46, 44, 54), width=stroke)
    # Progress
    if score is not None and score > 0:
        end_angle = -90 + (score / 100) * 360
        draw.arc([cx-radius, cy-radius, cx+radius, cy+radius], -90, end_angle, fill=color, width=stroke)

def _draw_dist_bar(draw, x, y, w, h, counts):
    """Draw a verdict distribution bar."""
    entries = [(v, n) for v, n in counts.items() if n > 0]
    total = sum(n for _, n in entries) or 1
    cx = x
    for v, n in entries:
        seg_w = int((n / total) * w)
        if seg_w < 1: seg_w = 1
        color = VERDICT_COLORS.get(v, TEXT3)
        draw.rounded_rectangle([cx, y, cx + seg_w, y + h], radius=2, fill=color)
        cx += seg_w

def _draw_wordmark(draw, x, y):
    """Draw a simplified VS wordmark."""
    # Wave
    points = []
    import math
    for i in range(40):
        px = x + i * 1.5
        py = y + 10 + math.sin(i * 0.35) * 8
        points.append((px, py))
    if len(points) >= 2:
        draw.line(points, fill=VIOLET, width=3)
    # Dot
    draw.ellipse([x + 65, y + 6, x + 73, y + 14], fill=PINK)
    # Text
    font = FONT_BOLD(16)
    draw.text((x + 80, y + 2), "VERUM", fill=WHITE, font=font)
    draw.text((x + 155, y + 2), "SIGNAL", fill=(192, 132, 252), font=FONT_REG(16))


def generate_report_og(source, score, title):
    """Generate OG image for an article report page."""
    img = Image.new('RGB', (1200, 630), BG)
    draw = ImageDraw.Draw(img)

    # Subtle gradient overlay at top
    for i in range(200):
        alpha = int(12 * (1 - i / 200))
        draw.rectangle([0, i, 1200, i + 1], fill=(VIOLET[0], VIOLET[1], VIOLET[2], alpha) if alpha > 0 else BG)

    # Wordmark top-left
    _draw_wordmark(draw, 48, 40)

    # "CREDIBILITY REPORT" eyebrow
    draw.text((48, 90), "CREDIBILITY REPORT", fill=TEXT3, font=FONT_MONO(14))

    # Score ring
    score_val = int(score) if score is not None else None
    _draw_score_ring(draw, 1060, 200, 80, score_val, stroke=8)
    # Score number inside ring
    score_text = str(score_val) if score_val is not None else "—"
    score_font = FONT_SERIF(48)
    bbox = draw.textbbox((0, 0), score_text, font=score_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((1060 - tw // 2, 200 - th // 2 - 5), score_text, fill=_score_color(score_val), font=score_font)
    # /100 label
    draw.text((1060 - 15, 255), "/100", fill=TEXT3, font=FONT_MONO(14))

    # Source
    draw.text((48, 140), str(source or ""), fill=VIOLET, font=FONT_BOLD(22))

    # Title (wrap at ~50 chars per line, max 3 lines)
    title_str = str(title or "")
    title_font = FONT_BOLD(28)
    lines = []
    words = title_str.split()
    current = ""
    for w in words:
        test = current + " " + w if current else w
        bbox = draw.textbbox((0, 0), test, font=title_font)
        if bbox[2] - bbox[0] > 850:
            if current: lines.append(current)
            current = w
        else:
            current = test
    if current: lines.append(current)
    lines = lines[:3]
    if len(lines) == 3 and len(words) > len(" ".join(lines).split()):
        lines[2] = lines[2][:60] + "..."

    y = 190
    for line in lines:
        draw.text((48, y), line, fill=WHITE, font=title_font)
        y += 40

    # "Signal through the noise" footer
    draw.text((48, 560), "verumsignal.com", fill=TEXT3, font=FONT_MONO(14))
    draw.text((48, 580), "Signal through the noise", fill=(107, 114, 128), font=FONT_REG(13))

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf


def generate_outlet_og(domain, score):
    """Generate OG image for an outlet detail page."""
    img = Image.new('RGB', (1200, 630), BG)
    draw = ImageDraw.Draw(img)

    _draw_wordmark(draw, 48, 40)
    draw.text((48, 90), "OUTLET RELIABILITY PROFILE", fill=TEXT3, font=FONT_MONO(14))

    # Outlet name large
    draw.text((48, 160), str(domain or ""), fill=WHITE, font=FONT_SERIF(52))

    # Score ring
    score_val = int(score) if score is not None and score != '' else None
    _draw_score_ring(draw, 600, 380, 100, score_val, stroke=10)
    score_text = str(score_val) if score_val is not None else "—"
    score_font = FONT_SERIF(64)
    bbox = draw.textbbox((0, 0), score_text, font=score_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((600 - tw // 2, 380 - th // 2 - 5), score_text, fill=_score_color(score_val), font=score_font)
    draw.text((600 - 15, 450), "/100", fill=TEXT3, font=FONT_MONO(16))

    draw.text((48, 560), "verumsignal.com", fill=TEXT3, font=FONT_MONO(14))
    draw.text((48, 580), "Signal through the noise", fill=TEXT3, font=FONT_REG(13))

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf


CLAIM_VERDICT_LABELS = {
    'supported': 'SUPPORTED', 'plausible': 'PLAUSIBLE', 'corroborated': 'CORROBORATED',
    'overstated': 'OVERSTATED', 'disputed': 'DISPUTED', 'not_supported': 'NOT SUPPORTED',
    'not_verifiable': 'NOT VERIFIABLE', 'opinion': 'OPINION',
}


def generate_claim_og(verdict, claim_text, context):
    """Generate OG image for a claim page (S13): verdict pill, claim text, who and where."""
    img = Image.new('RGB', (1200, 630), BG)
    draw = ImageDraw.Draw(img)

    _draw_wordmark(draw, 48, 40)
    draw.text((48, 90), "CLAIM", fill=TEXT3, font=FONT_MONO(14))

    # Verdict pill
    label = CLAIM_VERDICT_LABELS.get(verdict, str(verdict or '').upper().replace('_', ' '))
    pill_color = VERDICT_COLORS.get(verdict, TEXT3)
    lum = 0.299 * pill_color[0] + 0.587 * pill_color[1] + 0.114 * pill_color[2]
    pill_font = FONT_BOLD(20)
    bbox = draw.textbbox((0, 0), label, font=pill_font)
    pw, ph = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rounded_rectangle([48, 124, 48 + pw + 32, 124 + ph + 22], radius=8, fill=pill_color)
    draw.text((64, 124 + 11 - bbox[1]), label, fill=BG if lum > 140 else WHITE, font=pill_font)

    # Claim text (no quotation marks: extracted claims are not always verbatim); up to 5 lines
    text_font = FONT_BOLD(34)
    words = ' '.join(str(claim_text or '').split()).split(' ')
    lines, current = [], ''
    for w in words:
        test = current + ' ' + w if current else w
        if draw.textbbox((0, 0), test, font=text_font)[2] > 1100:
            if current:
                lines.append(current)
            current = w
        else:
            current = test
    if current:
        lines.append(current)
    if len(lines) > 5:
        lines = lines[:5]
        last = lines[4]
        while last and draw.textbbox((0, 0), last + '…', font=text_font)[2] > 1100:
            last = last[:-1]
        lines[4] = last.rstrip() + '…'
    y = 200
    for line in lines:
        draw.text((48, y), line, fill=WHITE, font=text_font)
        y += 48

    # Who and where
    ctx = ' '.join(str(context or '').split())
    ctx_font = FONT_REG(22)
    while ctx and draw.textbbox((0, 0), ctx, font=ctx_font)[2] > 1100:
        ctx = ctx[:-2].rstrip() + '…'
    draw.text((48, min(y + 22, 520)), ctx, fill=TEXT2, font=ctx_font)

    draw.text((48, 560), "verumsignal.com", fill=TEXT3, font=FONT_MONO(14))
    draw.text((48, 580), "Signal through the noise", fill=TEXT3, font=FONT_REG(13))

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf


def generate_debate_og(name, claims):
    """Generate OG image for a debate page."""
    img = Image.new('RGB', (1200, 630), BG)
    draw = ImageDraw.Draw(img)

    _draw_wordmark(draw, 48, 40)
    draw.text((48, 90), "LIVE DEBATE COVERAGE", fill=GREEN, font=FONT_MONO(14))

    # Event name (wrap)
    name_str = str(name or "")
    name_font = FONT_BOLD(32)
    lines = []
    words = name_str.split()
    current = ""
    for w in words:
        test = current + " " + w if current else w
        bbox = draw.textbbox((0, 0), test, font=name_font)
        if bbox[2] - bbox[0] > 1050:
            if current: lines.append(current)
            current = w
        else:
            current = test
    if current: lines.append(current)

    y = 180
    for line in lines[:3]:
        draw.text((48, y), line, fill=WHITE, font=name_font)
        y += 44

    # Claims count
    claims_str = str(claims or 0)
    draw.text((48, y + 30), claims_str, fill=VIOLET, font=FONT_SERIF(72))
    draw.text((48 + len(claims_str) * 42 + 10, y + 65), "claims evaluated", fill=TEXT2, font=FONT_REG(22))

    draw.text((48, 560), "verumsignal.com", fill=TEXT3, font=FONT_MONO(14))
    draw.text((48, 580), "Signal through the noise", fill=TEXT3, font=FONT_REG(13))

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf
