# -*- coding: utf-8 -*-
import os
import sys
import re
import threading
import multiprocessing
import pymupdf
from lxml import etree
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# DocBook 5.0 Namespaces
DOCBOOK_NS = "http://docbook.org/ns/docbook"
XLINK_NS = "http://www.w3.org/1999/xlink"
MML_NS = "http://www.w3.org/1998/Math/MathML"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NS_MAP = {
    None: DOCBOOK_NS,
    "xlink": XLINK_NS,
    "mml": MML_NS,
    "xml": XML_NS
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

VALID_COMPOUND_WORDS = {
    "point", "aware", "driven", "based", "level", "order", "state", "rate",
    "free", "bound", "scale", "wise", "width", "time", "domain", "end",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "first", "second", "third", "can", "catch", "as", "known", "built",
    "long", "short", "wide", "side", "line", "type", "fold", "page", "step", "established"
}

NUMBER_PREFIXES = {
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "well", "all"
}

PUNCTUATION_ENTITIES = {
    "&#x201C;", "&#x201D;", "&#x2018;", "&#x2019;", "&#x2013;", "&#x2014;", 
    "&#x2022;", "&#x2212;", "&#x2264;", "&#x2265;", "&#x2208;", "&#x0026;"
}

STANDALONE_WORDS = {
    "sich", "und", "der", "die", "das", "ein", "eine", "mit", "von", "zu",
    "auf", "im", "in", "den", "dem", "des", "nicht", "auch", "als", "an",
    "the", "and", "a", "an", "of", "in", "to", "for", "with", "on", "at"
}

SPEAKER_LABEL_REGEX = re.compile(r'^[A-Z0-9]{1,10}\s*:\s+')

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

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

def smart_title_case(text):
    if not text:
        return ""
    minor_words = {"and", "or", "but", "a", "an", "the", "as", "at", "by", "for", "in", "of", "on", "per", "to", "via"}
    words = text.split()
    res = []
    for i, w in enumerate(words):
        core = re.sub(r'[^a-zA-Z]', '', w).lower()
        if i > 0 and core in minor_words:
            res.append(w.lower())
        else:
            res.append(w.capitalize())
    return " ".join(res)

def fix_hyphenated_words(text):
    if not text:
        return ""

    text = text.replace('\u00ad', '').replace('\xad', '')

    def line_break_replacer(match):
        prefix = match.group(1)
        suffix = match.group(2)
        if prefix.lower() in NUMBER_PREFIXES or suffix.lower() in VALID_COMPOUND_WORDS:
            return f"{prefix}-{suffix}"
        return prefix + suffix

    text = re.sub(r'([a-zA-Z]{2,})[-‐‑]\s+([a-zA-Z]{2,})', line_break_replacer, text)
    return text

def fix_missing_boundary_spaces(text):
    if not text:
        return ""

    text = re.sub(r'([,;])([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'&#x201C;\s+', '&#x201C;', text)
    text = re.sub(r'\s+&#x201D;', '&#x201D;', text)
    text = re.sub(r'&#x2019;\s*s\b', '&#x2019;s', text)
    text = re.sub(r"'\s*s\b", "'s", text)
    text = re.sub(r'\s*&#x2013;\s*', '&#x2013;', text)
    text = re.sub(r'\s*&#x2014;\s*', '&#x2014;', text)
    text = re.sub(r'(&#x201D;|"|\))([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z])(&#x201C;|"|\()', r'\1 \2', text)

    def clean_intra_word_after(match):
        ent = match.group(1)
        suffix = match.group(2)
        if ent in PUNCTUATION_ENTITIES:
            return f"{ent} {suffix}"
        if suffix.lower() in STANDALONE_WORDS:
            return f"{ent} {suffix}"
        if len(suffix) <= 5 and suffix.islower():
            return f"{ent}{suffix}"
        return f"{ent} {suffix}"

    text = re.sub(r'(&#x[0-9A-Fa-f]+;)[ \t]+([a-zA-Z]{1,10})', clean_intra_word_after, text)

    def clean_intra_word_before(match):
        prefix = match.group(1)
        ent = match.group(2)
        if ent in PUNCTUATION_ENTITIES:
            return f"{prefix} {ent}"
        if prefix.lower() in STANDALONE_WORDS:
            return f"{prefix} {ent}"
        if len(prefix) <= 4 and prefix.islower():
            return f"{prefix}{ent}"
        return f"{prefix} {ent}"

    text = re.sub(r'([a-zA-Z]{1,10})[ \t]+(&#x[0-9A-Fa-f]+;)', clean_intra_word_before, text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text

def post_process_clean_xml(xml_str):
    if not xml_str:
        return ""
    
    xml_str = re.sub(r'&amp;#x([0-9A-Fa-f]+);', r'&#x\1;', xml_str)
    xml_str = re.sub(r'&#x00A0;', ' ', xml_str)
    
    xml_str = xml_str.replace("’", "&#x2019;")
    xml_str = xml_str.replace("‘", "&#x2018;")
    xml_str = xml_str.replace("“", "&#x201C;")
    xml_str = xml_str.replace("”", "&#x201D;")
    xml_str = xml_str.replace("–", "&#x2013;")
    xml_str = xml_str.replace("—", "&#x2014;")

    xml_str = re.sub(r'[ \t]+</para>', '</para>', xml_str)
    xml_str = re.sub(r'[ \t]+</title>', '</title>', xml_str)
    xml_str = re.sub(r'<para>[ \t]+', '<para>', xml_str)
    xml_str = re.sub(r'<title>[ \t]+', '<title>', xml_str)
    xml_str = re.sub(r'&#x201C;\s+', '&#x201C;', xml_str)
    xml_str = re.sub(r'\s+&#x201D;', '&#x201D;', xml_str)
    xml_str = re.sub(r'&#x2019;\s+s\b', '&#x2019;s', xml_str)
    xml_str = re.sub(r'\s*&#x2013;\s*', '&#x2013;', xml_str)
    xml_str = re.sub(r'\s*&#x2014;\s*', '&#x2014;', xml_str)

    xml_str = re.sub(r'<para\s*/>', '', xml_str)
    xml_str = re.sub(r'<para>\s*</para>', '', xml_str)
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

def append_styled_spans_to_node(target_elem, span_list, default_ns=DOCBOOK_NS):
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
            if len(target_elem) > 0:
                target_elem[-1].tail = (target_elem[-1].tail or "") + raw_text
            else:
                target_elem.text = (target_elem.text or "") + raw_text
            continue

        if leading_ws > 0:
            lead_str = " " * leading_ws
            if len(target_elem) > 0:
                target_elem[-1].tail = (target_elem[-1].tail or "") + lead_str
            else:
                target_elem.text = (target_elem.text or "") + lead_str

        container_elem = None
        leaf_node = None

        if "sup" in style or "sub" in style or "bold" in style or "italic" in style:
            curr_elem = target_elem
            if "sup" in style:
                curr_elem = etree.SubElement(curr_elem, f"{{{default_ns}}}superscript")
            elif "sub" in style:
                curr_elem = etree.SubElement(curr_elem, f"{{{default_ns}}}subscript")
            
            if "bold" in style:
                curr_elem = etree.SubElement(curr_elem, f"{{{default_ns}}}emphasis", attrib={"role": "bold"})
            if "italic" in style:
                curr_elem = etree.SubElement(curr_elem, f"{{{default_ns}}}emphasis", attrib={"role": "italic"})

            container_elem = target_elem[-1]
            leaf_node = curr_elem
        else:
            leaf_node = target_elem

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
                if len(target_elem) > 0:
                    target_elem[-1].tail = (target_elem[-1].tail or "") + trail_str
                else:
                    target_elem.text = (target_elem.text or "") + trail_str

def is_actual_running_header(line_text, y0, page_height):
    t = line_text.strip()
    if not t:
        return True
    if y0 < 55 or y0 > (page_height - 55):
        if re.search(r'^(?:\d+\s+)?Chapter\s+\d+', t, re.IGNORECASE) or re.search(r'Chapter\s+\d+\s+\d+$', t, re.IGNORECASE):
            return True
        if re.match(r'^\d{1,5}$', t):
            return True
        if len(t) < 45 and not t.endswith('.'):
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
            if y0 < 55 or y1 > page_height - 55:
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

def extract_pdf_pages_clean_header(pdf_path, status_callback=None):
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    page_records = []
    
    chapter_regex = re.compile(r'^(CHAPTER\s+\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE)\b', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    last_folio = None

    for idx, page in enumerate(doc, 1):
        if status_callback:
            status_callback(f"Extracting layout on page {idx}/{total_pages}...")

        detected_page_folio = extract_exact_page_number(page, last_folio)
        last_folio = detected_page_folio

        blocks_list = []
        page_dict = page.get_text("dict")
        page_height = page.rect.height
        page_blocks = page_dict.get("blocks", [])

        text_x0s = [b["bbox"][0] for b in page_blocks if b.get("type") == 0 and b.get("lines")]
        column_base_x0 = min(text_x0s) if text_x0s else 50.0

        for block in page_blocks:
            if block.get("type") != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue

            current_spans = []
            block_x0 = block["bbox"][0]
            base_x0 = lines[0]["bbox"][0]
            
            is_blockquote = (block_x0 - column_base_x0) > 30.0
            is_sidebar = (block_x0 - column_base_x0) > 18.0 and not is_blockquote
            is_indented = (base_x0 - column_base_x0) > 4.0

            prev_line_y1 = None
            prev_line_height = 12.0

            for line in lines:
                y0, y1 = line["bbox"][1], line["bbox"][3]
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
                dominant_size = max(set(sizes), key=sizes.count) if sizes else 10.0
                baseline_y = line_spans[0]["origin"][1] if "origin" in line_spans[0] else line["bbox"][3]

                line_x0 = line["bbox"][0]
                line_is_indented = (line_x0 - base_x0) > 4.0
                has_vertical_block_gap = (prev_line_y1 is not None) and ((y0 - prev_line_y1) > (prev_line_height * 0.35))

                if (line_is_indented or has_vertical_block_gap) and current_spans:
                    b_type = "blockquote" if is_blockquote else ("sidebar" if is_sidebar else "para")
                    blocks_list.append({
                        "type": b_type, 
                        "spans": current_spans, 
                        "raw": "".join([s["text"] for s in current_spans]).strip(),
                        "is_indented": is_indented
                    })
                    current_spans = []
                    base_x0 = line_x0

                raw_line_end = "".join([s.get("text", "") for s in line_spans]).rstrip()
                line_ends_with_hyphen = raw_line_end.endswith(('-', '‐', '‑', '\xad'))

                for s_i, span in enumerate(line_spans):
                    span_copy = dict(span)
                    s_text = span_copy.get("text", "")
                    if not s_text:
                        continue

                    if line_ends_with_hyphen and s_i == len(line_spans) - 1:
                        span_copy["text"] = re.sub(r'[-‐‑\xad]\s*$', '', span_copy["text"])

                    s_size = span_copy.get("size", dominant_size)
                    s_origin_y = span_copy.get("origin", (0, baseline_y))[1]

                    if s_size < dominant_size * 0.85:
                        span_copy["pos_type"] = "sup" if s_origin_y < baseline_y - 1.2 else ("sub" if s_origin_y > baseline_y + 1.0 else "regular")
                    else:
                        span_copy["pos_type"] = "regular"

                    if s_i < len(line_spans) - 1:
                        next_span_x0 = line_spans[s_i + 1]["bbox"][0]
                        curr_span_x1 = span["bbox"][2]
                        if (next_span_x0 - curr_span_x1) > 2.0 and not span_copy["text"].endswith(" "):
                            span_copy["text"] += " "

                    current_spans.append(span_copy)

                if current_spans and not line_ends_with_hyphen:
                    if not current_spans[-1]["text"].endswith(" "):
                        current_spans.append({"text": " ", "flags": 0, "size": dominant_size, "font": "", "pos_type": "regular"})

                is_chap = bool(chapter_regex.match(full_line_text) and len(full_line_text) < 40)
                is_sec = bool(sec_regex.match(full_line_text))
                is_caps_title = bool(
                    full_line_text.isupper() 
                    and any(c.isalpha() for c in full_line_text) 
                    and 3 < len(full_line_text) < 120 
                    and not full_line_text.endswith('.')
                    and max(sizes, default=0) >= dominant_size
                )

                if is_chap or is_sec or is_caps_title:
                    if current_spans:
                        b_type = "blockquote" if is_blockquote else ("sidebar" if is_sidebar else "para")
                        blocks_list.append({
                            "type": b_type, 
                            "spans": current_spans, 
                            "raw": "".join([s["text"] for s in current_spans]).strip(),
                            "is_indented": is_indented
                        })
                        current_spans = []
                    
                    kind = "chap_title" if is_chap else "heading"
                    blocks_list.append({"type": kind, "spans": line_spans, "raw": full_line_text})
                    base_x0 = line_x0
                    prev_line_y1 = y1
                    prev_line_height = line_height
                    continue

                prev_line_y1 = y1
                prev_line_height = line_height

            if current_spans:
                b_type = "blockquote" if is_blockquote else ("sidebar" if is_sidebar else "para")
                blocks_list.append({
                    "type": b_type, 
                    "spans": current_spans, 
                    "raw": "".join([s["text"] for s in current_spans]).strip(),
                    "is_indented": is_indented
                })

        page_records.append({"page_num": str(detected_page_folio), "blocks": blocks_list})

    return page_records

def parse_full_pdf(pdf_path, output_xml_path, doi, book_title, id_prefix, status_callback=None):
    if status_callback:
        status_callback("Analyzing PDF layout...")
    page_records = extract_pdf_pages_clean_header(pdf_path, status_callback)

    if status_callback:
        status_callback("Building DocBook XML document...")

    prefix = id_prefix.strip() if id_prefix.strip() else ("b-" + re.sub(r'[^a-zA-Z0-9]', '', os.path.splitext(os.path.basename(output_xml_path))[0]))
    id_counter = 1

    def next_id():
        nonlocal id_counter
        curr = f"{prefix}-{id_counter:07d}"
        id_counter += 1
        return curr

    root = etree.Element(
        f"{{{DOCBOOK_NS}}}book",
        attrib={
            "version": "5.0",
            f"{{{XML_NS}}}lang": "en",
            "role": "fullText",
            f"{{{XML_NS}}}id": prefix
        },
        nsmap=NS_MAP
    )

    # 1. Info Block
    info_elem = etree.SubElement(root, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}
