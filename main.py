import os
import re
import sys
import uuid
from datetime import datetime
from typing import List, Dict
import pymupdf
from lxml import etree
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
UPLOADS_DIR = os.path.join(os.getcwd(), "uploads")
OUTPUTS_DIR = os.path.join(os.getcwd(), "outputs")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

app = FastAPI(title="TTBS XML Studio")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

XML_NS = "http://www.w3.org/XML/1998/namespace"
XLINK_NS = "http://www.w3.org/1999/xlink"
NS_MAP = {
    "ali": "http://www.niso.org/schemas/ali/1.0/",
    "xlink": XLINK_NS,
    "mml": "http://www.w3.org/1998/Math/MathML",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance"
}

LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl"
}

ROMAN_TO_NUM = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10
}

PORTUGUESE_PRONOUNS_AND_SUFFIXES = {
    "se", "me", "te", "nos", "vos", "o", "a", "os", "as", "lhe", "lhes",
    "lo", "la", "los", "las", "no", "na", "nos", "nas"
}

VALID_COMPOUND_WORDS = {
    "point", "aware", "driven", "based", "level", "order", "state", "rate",
    "free", "bound", "scale", "wise", "width", "time", "domain", "end",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "first", "second", "third", "long", "short", "wide", "side", "line",
    "type", "fold", "page", "step", "established", "lei", "padrao", "padroes"
}

NUMBER_PREFIXES = {
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "well", "all",
    "ex", "vice", "pos", "pre", "pro", "sub", "super", "anti"
}

PUNCTUATION_ENTITIES = {
    "&#x201C;", "&#x201D;", "&#x2018;", "&#x2019;", "&#x2013;", "&#x2014;", 
    "&#x2022;", "&#x2212;", "&#x2264;", "&#x2265;", "&#x2208;"
}

STANDALONE_WORDS = {
    "sich", "und", "der", "die", "das", "ein", "eine", "mit", "von", "zu",
    "auf", "im", "in", "den", "dem", "des", "nicht", "auch", "als", "an",
    "the", "and", "a", "an", "of", "in", "to", "for", "with", "on", "at",
    "de", "do", "da", "dos", "das", "em", "um", "uma", "com", "por", "para", "ou", "e"
}

SPEAKER_LABEL_REGEX = re.compile(r'^[A-Z0-9]{1,10}\s*:\s+')

def clean_to_hex_entities(text):
    if not text:
        return ""
    for lig, replacement in LIGATURE_MAP.items():
        text = text.replace(lig, replacement)

    out_chars = []
    for char in text:
        cp = ord(char)
        if cp > 127:
            if cp <= 0xFFFF:
                out_chars.append(f"&#x{cp:04X};")
            else:
                out_chars.append(f"&#x{cp:06X};")
        else:
            out_chars.append(char)
    text = "".join(out_chars)

    valid_xml = re.compile(r'[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')
    return valid_xml.sub('', text)

def fix_hyphenated_words(text):
    if not text:
        return ""
    text = re.sub(r'\b(inter|intra|pre|post|macro|micro)and\b', r'\1- and', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(inter|intra|pre|post|macro|micro)or\b', r'\1- or', text, flags=re.IGNORECASE)
    text = text.replace('\u00ad', '').replace('\xad', '')

    def line_break_replacer(match):
        prefix = match.group(1)
        suffix = match.group(3)
        p_low = prefix.lower()
        s_low = suffix.lower()

        if s_low in {"and", "or", "to", "und", "e", "ou"}:
            return f"{prefix}- {suffix}"
        if s_low in PORTUGUESE_PRONOUNS_AND_SUFFIXES or s_low in VALID_COMPOUND_WORDS or p_low in NUMBER_PREFIXES:
            return f"{prefix}-{suffix}"
        return f"{prefix}{suffix}"

    text = re.sub(r'([a-zA-ZÀ-ÿ]{2,})([-‐‑])\s+([a-zA-ZÀ-ÿ]{2,})', line_break_replacer, text)
    return text

def fix_missing_boundary_spaces(text):
    if not text:
        return ""

    text = re.sub(r'([,;])([A-Za-zÀ-ÿ])', r'\1 \2', text)
    text = re.sub(r'&#x201C;\s+', '&#x201C;', text)
    text = re.sub(r'\s+&#x201D;', '&#x201D;', text)
    text = re.sub(r'(&#x2019;|\')\s*(s|t|d|m|re|ve|ll|he|em)\b', r'\1\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*&#x2013;\s*', '&#x2013;', text)
    text = re.sub(r'\s*&#x2014;\s*', '&#x2014;', text)
    text = re.sub(r'(&#x201D;|"|\))([A-Za-zÀ-ÿ])', r'\1 \2', text)
    text = re.sub(r'([A-Za-zÀ-ÿ])(&#x201C;|"|\()', r'\1 \2', text)

    def clean_intra_word_after(match):
        ent = match.group(1)
        suffix = match.group(2)
        if ent in {"&#x2019;", "&#x0027;"}:
            return f"{ent}{suffix}"
        if ent in PUNCTUATION_ENTITIES or suffix.lower() in STANDALONE_WORDS:
            return f"{ent} {suffix}"
        if len(suffix) <= 5 and suffix.islower():
            return f"{ent}{suffix}"
        return f"{ent} {suffix}"

    text = re.sub(r'(&#x[0-9A-Fa-f]+;)[ \t]+([a-zA-ZÀ-ÿ]{1,10})', clean_intra_word_after, text)

    def clean_intra_word_before(match):
        prefix = match.group(1)
        ent = match.group(2)
        if ent in PUNCTUATION_ENTITIES or prefix.lower() in STANDALONE_WORDS:
            return f"{prefix} {ent}"
        if len(prefix) <= 4 and prefix.islower():
            return f"{prefix}{ent}"
        return f"{prefix} {ent}"

    text = re.sub(r'([a-zA-ZÀ-ÿ]{1,10})[ \t]+(&#x[0-9A-Fa-f]+;)', clean_intra_word_before, text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text

def post_process_clean_xml(xml_str):
    if not xml_str:
        return ""
    
    xml_str = re.sub(r'&amp;#x([0-9A-Fa-f]+);', r'&#x\1;', xml_str)
    xml_str = re.sub(r'&#x00A0;', ' ', xml_str)
    xml_str = re.sub(r'[ \t]+</p>', '</p>', xml_str)
    xml_str = re.sub(r'[ \t]+</sec>', '</sec>', xml_str)
    xml_str = re.sub(r'[ \t]+</chapter>', '</chapter>', xml_str)
    xml_str = re.sub(r'[ \t]+</title>', '</title>', xml_str)
    xml_str = re.sub(r'[ \t]+</label>', '</label>', xml_str)
    xml_str = re.sub(r'[ \t]+</blockquote>', '</blockquote>', xml_str)
    xml_str = re.sub(r'<p>[ \t]+', '<p>', xml_str)
    xml_str = re.sub(r'<title>[ \t]+', '<title>', xml_str)
    xml_str = re.sub(r'&#x201C;\s+', '&#x201C;', xml_str)
    xml_str = re.sub(r'\s+&#x201D;', '&#x201D;', xml_str)
    xml_str = re.sub(r'(&#x2019;|\')\s+(s|t|d|m|re|ve|ll|he|em)\b', r'\1\2', xml_str, flags=re.IGNORECASE)
    xml_str = re.sub(r'\b(inter|intra|pre|post|macro|micro)and\b', r'\1- and', xml_str, flags=re.IGNORECASE)
    xml_str = re.sub(r'\b(inter|intra|pre|post|macro|micro)or\b', r'\1- or', xml_str, flags=re.IGNORECASE)
    xml_str = re.sub(r'\s*&#x2013;\s*', '&#x2013;', xml_str)
    xml_str = re.sub(r'\s*&#x2014;\s*', '&#x2014;', xml_str)
    xml_str = re.sub(r'<p>\s*</p>', '', xml_str)
    xml_str = re.sub(r'<blockquote>\s*</blockquote>', '', xml_str)
    return xml_str

def merge_consecutive_styled_spans(span_list):
    if not span_list:
        return []

    merged = []
    current_text = ""
    current_style = None

    for span in span_list:
        text = clean_to_hex_entities(span.get("text", ""))
        text = fix_hyphenated_words(text)
        text = fix_missing_boundary_spaces(text)
        if not text:
            continue

        font = span.get("font", "").lower()
        flags = span.get("flags", 0)
        pos_type = span.get("pos_type", "regular")

        is_bold = (flags & 2**4) != 0 or "bold" in font or "black" in font
        is_italic = (flags & 2**1) != 0 or "italic" in font or "oblique" in font

        style_parts = []
        if pos_type in ("sup", "sub"):
            style_parts.append(pos_type)
        if is_bold:
            style_parts.append("bold")
        if is_italic:
            style_parts.append("italic")

        style = "_".join(style_parts) if style_parts else "regular"

        if text.isspace() and current_style is not None:
            current_text += text
            continue

        if current_style is None:
            current_style = style
            current_text = text
        elif current_style == style:
            current_text += text
        else:
            if current_text:
                merged.append({"style": current_style, "text": current_text})
            current_style = style
            current_text = text

    if current_text:
        merged.append({"style": current_style, "text": current_text})

    return merged

def append_styled_spans_to_node(target_p, span_list):
    merged_spans = merge_consecutive_styled_spans(span_list)

    for item in merged_spans:
        raw_text = item["text"]
        style = item["style"]

        if not raw_text:
            continue

        leading_ws = len(raw_text) - len(raw_text.lstrip(' '))
        trailing_ws = len(raw_text) - len(raw_text.rstrip(' '))
        core_text = raw_text.strip(' ')

        if not core_text:
            if len(target_p) > 0:
                target_p[-1].tail = (target_p[-1].tail or "") + raw_text
            else:
                target_p.text = (target_p.text or "") + raw_text
            continue

        if leading_ws > 0:
            lead_str = " " * leading_ws
            if len(target_p) > 0:
                target_p[-1].tail = (target_p[-1].tail or "") + lead_str
            else:
                target_p.text = (target_p.text or "") + lead_str

        container_elem = None
        leaf_node = None

        if "sup" in style or "sub" in style or "bold" in style or "italic" in style:
            tag_order = []
            if "sup" in style:
                tag_order.append("sup")
            elif "sub" in style:
                tag_order.append("sub")
            if "bold" in style:
                tag_order.append("bold")
            if "italic" in style:
                tag_order.append("italic")

            container_elem = etree.SubElement(target_p, tag_order[0])
            curr_elem = container_elem
            for next_tag in tag_order[1:]:
                curr_elem = etree.SubElement(curr_elem, next_tag)
            leaf_node = curr_elem
        else:
            leaf_node = target_p

        if len(leaf_node) > 0:
            if leaf_node[-1].tail:
                leaf_node[-1].tail += core_text
            else:
                leaf_node[-1].tail = core_text
        else:
            leaf_node.text = (leaf_node.text or "") + core_text

        if trailing_ws > 0:
            trail_str = " " * trailing_ws
            if container_elem is not None:
                container_elem.tail = (container_elem.tail or "") + trail_str
            else:
                if len(target_p) > 0:
                    target_p[-1].tail = (target_p[-1].tail or "") + trail_str
                else:
                    target_p.text = (target_p.text or "") + trail_str

def is_actual_running_header(line_text, y0, page_height):
    t = line_text.strip()
    if not t:
        return True
    if y0 < 55 or y0 > (page_height - 55):
        if re.search(r'^(?:\d+\s+)?Chapter\s+\d+', t, re.IGNORECASE) or re.search(r'Chapter\s+\d+\s+\d+$', t, re.IGNORECASE):
            return True
        if re.match(r'^\d{1,5}$', t):
            return True
        if len(t) < 50 and not t.endswith('.'):
            return True
    return False

def extract_exact_page_number(page, last_confirmed_page):
    page_dict = page.get_text("dict")
    page_width = page.rect.width
    page_height = page.rect.height
    detected_folio = None

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            x0, y0, x1, y1 = line["bbox"]
            is_header_zone = (y0 < 55)
            is_footer_zone = (y1 > page_height - 55)

            if is_header_zone or is_footer_zone:
                line_text = "".join([s.get("text", "") for s in line.get("spans", [])]).strip()
                if not line_text:
                    continue

                if line_text.isdigit():
                    val = int(line_text)
                    if 1 <= val <= 99999:
                        detected_folio = val
                        break

                left_match = re.match(r'^(\d{1,5})\b', line_text)
                if left_match and x0 < page_width * 0.40:
                    detected_folio = int(left_match.group(1))
                    break

                right_match = re.search(r'\b(\d{1,5})$', line_text)
                if right_match and x1 > page_width * 0.60:
                    detected_folio = int(right_match.group(1))
                    break

        if detected_folio is not None:
            break

    if detected_folio is None:
        detected_folio = (last_confirmed_page + 1) if last_confirmed_page is not None else (page.number + 1)

    return detected_folio

def extract_pdf_pages_clean_header(pdf_path):
    doc = pymupdf.open(pdf_path)
    page_records = []
    
    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    subsubsec_regex = re.compile(r'^(\d+)\)\s*(.*)')
    ref_item_regex = re.compile(r'^\[(\d+)\]\s+(.*)')

    last_folio = None

    for page in doc:
        detected_page_folio = extract_exact_page_number(page, last_folio)
        last_folio = detected_page_folio

        blocks_list = []
        page_dict = page.get_text("dict")
        page_height = page.rect.height
        page_blocks = page_dict.get("blocks", [])

        body_lines_x0 = []
        body_lines_x1 = []
        body_font_sizes = []
        for b in page_blocks:
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                y0, y1 = ln["bbox"][1], ln["bbox"][3]
                if y0 >= 55 and y1 <= (page_height - 55):
                    body_lines_x0.append(ln["bbox"][0])
                    body_lines_x1.append(ln["bbox"][2])
                    for sp in ln.get("spans", []):
                        if sp.get("text", "").strip():
                            body_font_sizes.append(round(sp.get("size", 10.0), 1))

        if body_lines_x0:
            rounded_x0s = [round(x, 0) for x in body_lines_x0 if x >= 30]
            column_base_x0 = max(set(rounded_x0s), key=rounded_x0s.count) if rounded_x0s else min(body_lines_x0)
            rounded_x1s = [round(x, 0) for x in body_lines_x1]
            column_base_x1 = max(rounded_x1s) if rounded_x1s else 500.0
        else:
            column_base_x0 = 50.0
            column_base_x1 = 500.0

        dominant_page_size = max(set(body_font_sizes), key=body_font_sizes.count) if body_font_sizes else 10.0

        for block in page_blocks:
            if block.get("type") != 0:
                continue
            
            lines = block.get("lines", [])
            if not lines:
                continue

            # Strict blockquote inspection: all lines indented from margin
            is_blockquote = False
            if len(lines) >= 2:
                all_lines_left_indented = all(
                    (ln["bbox"][0] - column_base_x0) >= 14.0
                    for ln in lines if "".join([s.get("text", "") for s in ln.get("spans", [])]).strip()
                )
                avg_line_right = sum([ln["bbox"][2] for ln in lines]) / len(lines)
                has_right_indent = (column_base_x1 - avg_line_right) >= 8.0

                if all_lines_left_indented and has_right_indent:
                    is_blockquote = True

            current_spans = []
            base_x0 = lines[0]["bbox"][0]
            prev_line_y1 = None
            prev_line_height = 12.0

            for line_idx, line in enumerate(lines):
                y0 = line["bbox"][1]
                y1 = line["bbox"][3]
                line_height = y1 - y0
                line_spans = line.get("spans", [])
                if not line_spans:
                    continue

                full_line_text = clean_to_hex_entities("".join([s["text"] for s in line_spans])).strip()
                if not full_line_text:
                    continue

                if is_actual_running_header(full_line_text, y0, page_height):
                    continue

                sizes = [s["size"] for s in line_spans if s.get("text", "").strip()]
                dominant_size = max(set(sizes), key=sizes.count) if sizes else dominant_page_size
                baseline_y = line_spans[0]["origin"][1] if "origin" in line_spans[0] else line["bbox"][3]

                line_x0 = line["bbox"][0]
                is_first_line_p_indent = (not is_blockquote) and ((line_x0 - base_x0) > 4.0)
                
                has_vertical_block_gap = False
                if prev_line_y1 is not None:
                    gap = y0 - prev_line_y1
                    if gap > (prev_line_height * 0.40):
                        has_vertical_block_gap = True

                is_speaker_dialogue = bool(SPEAKER_LABEL_REGEX.match(full_line_text))

                if (is_first_line_p_indent or has_vertical_block_gap or is_speaker_dialogue) and current_spans:
                    block_kind = "blockquote" if is_blockquote else "para"
                    blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                    current_spans = []
                    base_x0 = line_x0

                raw_line_end = "".join([s.get("text", "") for s in line_spans]).rstrip()
                line_ends_with_hyphen = bool(re.search(r'[a-zA-ZÀ-ÿ][-‐‑\xad]$', raw_line_end))

                is_suspended_hyphen = False
                if line_ends_with_hyphen and (line_idx + 1 < len(lines)):
                    next_first_txt = "".join([s.get("text", "") for s in lines[line_idx + 1].get("spans", [])]).strip()
                    if re.match(r'^(and|or|to|und|e|ou)\b', next_first_txt, re.IGNORECASE):
                        is_suspended_hyphen = True

                for s_i, span in enumerate(line_spans):
                    span_copy = dict(span)
                    s_text = span_copy.get("text", "")
                    if not s_text:
                        continue

                    if line_ends_with_hyphen and s_i == len(line_spans) - 1:
                        if not is_suspended_hyphen:
                            span_copy["text"] = re.sub(r'[-‐‑\xad]\s*$', '', span_copy["text"])
                        else:
                            span_copy["text"] = re.sub(r'[-‐‑\xad]\s*$', '-', span_copy["text"])

                    s_size = span_copy.get("size", dominant_size)
                    s_origin_y = span_copy.get("origin", (0, baseline_y))[1]

                    if s_size < dominant_size * 0.85:
                        if s_origin_y < baseline_y - 1.2:
                            span_copy["pos_type"] = "sup"
                        elif s_origin_y > baseline_y + 1.0:
                            span_copy["pos_type"] = "sub"
                        else:
                            span_copy["pos_type"] = "regular"
                    else:
                        span_copy["pos_type"] = "regular"

                    if s_i < len(line_spans) - 1:
                        next_span_x0 = line_spans[s_i + 1]["bbox"][0]
                        curr_span_x1 = span["bbox"][2]
                        if (next_span_x0 - curr_span_x1) > 2.0 and not span_copy["text"].endswith(" "):
                            span_copy["text"] += " "

                    current_spans.append(span_copy)

                if current_spans and (not line_ends_with_hyphen or is_suspended_hyphen):
                    if not current_spans[-1]["text"].endswith(" "):
                        current_spans.append({"text": " ", "flags": 0, "size": dominant_size, "font": "", "pos_type": "regular"})

                # Section/Chapter Heading Check
                if (chapter_regex.match(full_line_text) or sec_regex.match(full_line_text) or 
                    subsec_regex.match(full_line_text) or subsubsec_regex.match(full_line_text) or 
                    ref_item_regex.match(full_line_text) or full_line_text.startswith("REFERENCES") or 
                    full_line_text.startswith("References")):
                    
                    if current_spans:
                        block_kind = "blockquote" if is_blockquote else "para"
                        blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                        current_spans = []
                    blocks_list.append({"type": "heading", "spans": line_spans, "raw": full_line_text})
                    base_x0 = line_x0
                    prev_line_y1 = y1
                    prev_line_height = line_height
                    continue

                prev_line_y1 = y1
                prev_line_height = line_height

            if current_spans:
                block_kind = "blockquote" if is_blockquote else "para"
                blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})

        page_records.append({"page_num": str(detected_page_folio), "blocks": blocks_list})

    doc.close()
    return page_records

def parse_full_pdf(pdf_path, output_xml_path, doi="10.1109/LWC.2025.3627417", journal_title="IEEE Wireless Communications Letters"):
    page_records = extract_pdf_pages_clean_header(pdf_path)

    root = etree.Element(
        "book",
        attrib={
            "dtd-version": "2.0",
            f"{{{XML_NS}}}lang": "eng"
        },
        nsmap=NS_MAP
    )

    # 1. Book Front Matter
    front = etree.SubElement(root, "front")
    b_meta = etree.SubElement(front, "book-meta")
    etree.SubElement(etree.SubElement(b_meta, "book-title-group"), "book-title").text = journal_title
    etree.SubElement(b_meta, "object-id", attrib={"pub-id-type": "doi"}).text = doi

    # 2. Body Matter with <chapter> Tagging
    body = etree.SubElement(root, "body")
    current_chapter = None
    current_sec = None
    current_subsec = None
    current_subsubsec = None
    
    chapter_count = 0
    current_sec_num = 1
    current_subsec_char = "a"
    in_references = False
    ref_items = []

    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    subsubsec_regex = re.compile(r'^(\d+)\)\s*(.*)')
    ref_item_regex = re.compile(r'^\[(\d+)\]\s+(.*)')

    for precord in page_records:
        page_num = precord["page_num"]
        blocks_to_process = precord["blocks"]
        page_marker_inserted = False

        for block in blocks_to_process:
            raw_txt = block["raw"]
            block_type = block.get("type", "para")

            if raw_txt.startswith("REFERENCES") or raw_txt.startswith("References"):
                in_references = True
                continue

            if in_references:
                ref_match = ref_item_regex.match(raw_txt)
                if ref_match:
                    ref_items.append((ref_match.group(1), ref_match.group(2)))
                elif ref_items:
                    last_n, last_t = ref_items[-1]
                    ref_items[-1] = (last_n, last_t + " " + raw_txt)
                continue

            # CHAPTER MATCHING (e.g., "Chapter 1 Introduction" or "CHAPTER I")
            chap_match = chapter_regex.match(raw_txt)
            if chap_match:
                chapter_count += 1
                c_num = chap_match.group(1) or chap_match.group(3) or str(chapter_count)
                c_title = chap_match.group(2).strip() if chap_match.group(2) else f"Chapter {c_num}"

                current_chapter = etree.SubElement(body, "chapter")
                current_chapter.set("id", f"chap{chapter_count}")

                page_marker = etree.SubElement(current_chapter, "named-content")
                page_marker.set("content-type", "page-id")
                page_marker.set("id", f"page-{page_num}")
                page_marker_inserted = True

                etree.SubElement(current_chapter, "label").text = f"Chapter {c_num}"
                etree.SubElement(current_chapter, "title").text = c_title
                
                current_sec = None
                current_subsec = None
                current_subsubsec = None
                continue

            # Default to chapter container if no chapter heading detected yet
            if current_chapter is None:
                chapter_count += 1
                current_chapter = etree.SubElement(body, "chapter")
                current_chapter.set("id", f"chap{chapter_count}")
                etree.SubElement(current_chapter, "label").text = f"Chapter {chapter_count}"
                etree.SubElement(current_chapter, "title").text = "Introduction"

            # Major Section
            sec_match = sec_regex.match(raw_txt)
            if sec_match:
                roman_val = sec_match.group(1).upper()
                sec_num = ROMAN_TO_NUM.get(roman_val, current_sec_num)
                current_sec_num = sec_num
                
                current_sec = etree.SubElement(current_chapter, "sec")
                current_sec.set("id", f"sec{sec_num}")

                if not page_marker_inserted:
                    page_marker = etree.SubElement(current_sec, "named-content")
                    page_marker.set("content-type", "page-id")
                    page_marker.set("id", f"page-{page_num}")
                    page_marker_inserted = True

                etree.SubElement(current_sec, "label").text = sec_match.group(1) + "."
                etree.SubElement(current_sec, "title").text = sec_match.group(2).strip()
                current_subsec = None
                current_subsubsec = None
                continue

            parent_target = current_sec if current_sec is not None else current_chapter

            # Subsection
            subsec_match = subsec_regex.match(raw_txt)
            if subsec_match and len(raw_txt) < 60:
                char_val = subsec_match.group(1).lower()
                current_subsec_char = char_val
                
                current_subsec = etree.SubElement(parent_target, "sec")
                current_subsec.set("id", f"sec{current_sec_num}{char_val}")

                if not page_marker_inserted:
                    page_marker = etree.SubElement(current_subsec, "named-content")
                    page_marker.set("content-type", "page-id")
                    page_marker.set("id", f"page-{page_num}")
                    page_marker_inserted = True

                etree.SubElement(current_subsec, "label").text = subsec_match.group(1) + "."
                etree.SubElement(current_subsec, "title").text = subsec_match.group(2).strip()
                current_subsubsec = None
                continue

            # Sub-subsection
            subsubsec_match = subsubsec_regex.match(raw_txt)
            if subsubsec_match and len(raw_txt) < 60:
                num_val = subsubsec_match.group(1)
                target_sub = current_subsec if current_subsec is not None else parent_target
                
                current_subsubsec = etree.SubElement(target_sub, "sec")
                current_subsubsec.set("id", f"sec{current_sec_num}{current_subsec_char}{num_val}")

                if not page_marker_inserted:
                    page_marker = etree.SubElement(current_subsubsec, "named-content")
                    page_marker.set("content-type", "page-id")
                    page_marker.set("id", f"page-{page_num}")
                    page_marker_inserted = True

                etree.SubElement(current_subsubsec, "label").text = f"{num_val})"
                etree.SubElement(current_subsubsec, "title").text = subsubsec_match.group(2).strip()
                continue

            active_parent = current_subsubsec if current_subsubsec is not None else (current_subsec if current_subsec is not None else current_chapter)

            if not page_marker_inserted:
                page_marker = etree.SubElement(active_parent, "named-content")
                page_marker.set("content-type", "page-id")
                page_marker.set("id", f"page-{page_num}")
                page_marker_inserted = True

            if block_type == "blockquote":
                quote_node = etree.SubElement(active_parent, "blockquote")
                p_node = etree.SubElement(quote_node, "p")
                append_styled_spans_to_node(p_node, block["spans"])
            else:
                p_node = etree.SubElement(active_parent, "p")
                append_styled_spans_to_node(p_node, block["spans"])

    # 3. Back Matter
    back = etree.SubElement(root, "back")
    ref_list = etree.SubElement(back, "ref-list")
    etree.SubElement(ref_list, "title").text = "References"

    for r_num, r_text in ref_items:
        clean_ref = clean_to_hex_entities(r_text).strip()
        clean_ref = fix_hyphenated_words(clean_ref)
        clean_ref = fix_missing_boundary_spaces(clean_ref)

        ref_elem = etree.SubElement(ref_list, "ref", attrib={"id": f"ref{r_num}"})
        etree.SubElement(ref_elem, "label").text = f"[{r_num}]"
        
        mix_cit = etree.SubElement(
            ref_elem, 
            "mixed-citation", 
            attrib={"publication-type": "other", "publication-format": "print"}
        )
        mix_cit.text = clean_ref

    doctype = '<!DOCTYPE book PUBLIC "-//NLM//DTD BITS Book Interchange DTD v2.0//EN" "BITS-book2.dtd">'
    raw_xml = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        doctype=doctype
    ).decode("utf-8")
    
    clean_xml = post_process_clean_xml(raw_xml)

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(clean_xml)

# -------------------------------------------------------------
# FASTAPI CONTROLLERS
# -------------------------------------------------------------
conversion_history: List[Dict] = []

@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/history")
async def get_history():
    return JSONResponse(conversion_history)

@app.post("/api/upload")
async def handle_upload(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())[:8]
    saved_filename = f"{file_id}_{file.filename}"
    saved_path = os.path.join(UPLOADS_DIR, saved_filename)

    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    doc = pymupdf.open(saved_path)
    total_pages = len(doc)
    doc.close()

    size_mb = f"{len(content) / (1024 * 1024):.2f} MB"
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    new_record = {
        "id": str(len(conversion_history) + 1),
        "name": file.filename,
        "date": now_str,
        "size": size_mb,
        "status": "Ready",
        "file_id": file_id,
        "pages": total_pages,
        "file_path": saved_path
    }
    conversion_history.insert(0, new_record)
    return JSONResponse({"status": "success", "file_info": new_record})

@app.post("/api/convert/{file_id}")
async def run_conversion(file_id: str):
    target = next((item for item in conversion_history if item.get("file_id") == file_id), None)
    if not target or not os.path.exists(target.get("file_path", "")):
        return JSONResponse({"status": "error", "message": "Source PDF file not found."}, status_code=404)

    target["status"] = "Processing"
    pdf_path = target["file_path"]
    base_name = os.path.splitext(target["name"])[0]
    out_xml_name = f"{base_name}.xml"
    out_xml_path = os.path.join(OUTPUTS_DIR, f"{file_id}_{out_xml_name}")

    try:
        parse_full_pdf(pdf_path, out_xml_path)

        target["status"] = "Done"
        target["xml_path"] = out_xml_path
        target["xml_filename"] = out_xml_name

        return JSONResponse({
            "status": "success",
            "download_url": f"/api/download/{target['id']}",
            "filename": out_xml_name
        })

    except Exception as err:
        target["status"] = "Failed"
        return JSONResponse({"status": "error", "message": str(err)}, status_code=500)

@app.get("/api/download/{item_id}")
async def download_xml_file(item_id: str):
    target = next((item for item in conversion_history if item.get("id") == item_id), None)
    if not target or "xml_path" not in target:
        return JSONResponse({"status": "error", "message": "XML output file not found."}, status_code=404)

    return FileResponse(
        target["xml_path"],
        filename=target.get("xml_filename", "document.xml"),
        media_type="application/xml"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)