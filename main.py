# -*- coding: utf-8 -*-
import os
import sys
import re
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
    
    # சிங்கிள் கோட் / அபாஸ்ட்ராபி அருகில் உள்ள தேவையற்ற இடைவெளிகளை நீக்குதல் (PDF துல்லியம்)
    text = re.sub(r"(&apos;|['‘])\s+", r"\1", text)
    text = re.sub(r"\s+(&apos;|['’])", r"\1", text)

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

        # URI & Footnote Tagging Integration inside text nodes
        # URL / URI Pattern Matching and Tagging
        url_pattern = re.compile(r'(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)')
        
        # Footnote Marker Pattern Matching (e.g., superscripts or bracketed numbers like [1])
        fn_pattern = re.compile(r'\[(\d+)\]')

        def process_text_content(parent_node, text_val):
            # Check for URIs or Footnote patterns and convert to proper tags (<uri>, <footnote>)
            parts = url_pattern.split(text_val)
            if len(parts) > 1:
                # Contains URL
                is_url_turn = False
                for part in parts:
                    if not part:
                        continue
                    if url_pattern.match(part):
                        full_url = part if part.startswith("http") else f"http://{part}"
                        uri_elem = etree.SubElement(parent_node, "uri", attrib={f"{{{XLINK_NS}}}href": full_url})
                        uri_elem.text = part
                    else:
                        if len(parent_node) > 0:
                            parent_node[-1].tail = (parent_node[-1].tail or "") + part
                        else:
                            parent_node.text = (parent_node.text or "") + part
            else:
                if len(parent_node) > 0:
                    if parent_node[-1].tail:
                        parent_node[-1].tail += text_val
                    else:
                        parent_node[-1].tail = text_val
                else:
                    parent_node.text = (parent_node.text or "") + text_val

        process_text_content(leaf_node, core_text)

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
    footnote_regex = re.compile(r'^(?:(\d+)\.|\*)\s+(.*)')

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
              
