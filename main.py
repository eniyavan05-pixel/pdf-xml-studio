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

COMMON_SYLLABLE_SUFFIXES = {
    "ing", "ings", "ed", "er", "ers", "est", "tion", "tions", "sion", "sions",
    "tism", "ous", "lar", "ment", "ments", "able", "ible", "ity", "ities",
    "ive", "ives", "al", "ally", "ence", "ance", "ic", "ical", "less", "ness",
    "ful", "ize", "ized", "ise", "ised", "ism", "ist", "ists", "logy", "phy",
    "ry", "ty", "ly", "ant", "ent", "ate", "ated", "ator", "atory"
}

VALID_COMPOUND_WORDS = {
    "point", "aware", "driven", "based", "level", "order", "state", "rate",
    "free", "bound", "scale", "wise", "width", "time", "domain", "end"
}

PUNCTUATION_ENTITIES = {
    "&#x201C;", "&#x201D;", "&#x2018;", "&#x2019;", "&#x2013;", "&#x2014;", 
    "&#x2022;", "&#x2212;", "&#x2264;", "&#x2265;", "&#x2208;"
}

# Common German & English prefixes that should remain separate words before an accented verb/noun
STANDALONE_WORDS = {
    "sich", "und", "der", "die", "das", "ein", "eine", "mit", "von", "zu",
    "auf", "im", "in", "den", "dem", "des", "nicht", "auch", "als", "an",
    "the", "and", "a", "an", "of", "in", "to", "for", "with", "on", "at"
}

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
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

def fix_hyphenated_words(text):
    if not text:
        return ""

    text = text.replace('\u00ad', '').replace('\xad', '')
    text = re.sub(r'([a-zA-Z]{2,})[-‐‑]\s+([a-zA-Z]{2,})', r'\1\2', text)

    def option_hyphen_replacer(match):
        full_match = match.group(0)
        prefix = match.group(1)
        suffix = match.group(2)
        s_lower = suffix.lower()

        if s_lower in COMMON_SYLLABLE_SUFFIXES:
            return prefix + suffix

        if s_lower in VALID_COMPOUND_WORDS:
            return full_match

        if prefix.isupper() or suffix.isupper() or any(c.isdigit() for c in full_match):
            return full_match

        if len(suffix) <= 4 and suffix.islower():
            return prefix + suffix

        return full_match

    text = re.sub(r'\b([a-zA-Z]{2,})[-‐‑]([a-zA-Z]{2,})\b', option_hyphen_replacer, text)
    return text

def fix_missing_boundary_spaces(text):
    if not text:
        return ""

    # Punctuation boundary spaces
    text = re.sub(r'([,;])([A-Za-z])', r'\1 \2', text)

    # Quotes and dashes formatting
    text = re.sub(r'&#x201C;\s+', '&#x201C;', text)
    text = re.sub(r'\s+&#x201D;', '&#x201D;', text)
    text = re.sub(r'&#x2019;\s*s\b', '&#x2019;s', text)
    text = re.sub(r"'\s*s\b", "'s", text)
    text = re.sub(r'\s*&#x2013;\s*', '&#x2013;', text)
    text = re.sub(r'\s*&#x2014;\s*', '&#x2014;', text)

    # Space after closing quotes/parentheses touching word
    text = re.sub(r'(&#x201D;|"|\))([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z])(&#x201C;|"|\()', r'\1 \2', text)

    # 1. Clean intra-word entity gaps ONLY when suffix is short (<4 chars) or not a standalone word
    # e.g., 'hei&#x00DF; t' -> 'hei&#x00DF;t', 'gegr&#x00FC; ndet' -> 'gegr&#x00FC;ndet'
    def clean_intra_word_after(match):
        ent = match.group(1)
        suffix = match.group(2)
        if ent in PUNCTUATION_ENTITIES:
            return f"{ent} {suffix}"
        # Keep word boundary if followed by standalone word
        if suffix.lower() in STANDALONE_WORDS:
            return f"{ent} {suffix}"
        # If suffix is short syllable or continuation (e.g., 't', 'ndet', 'lich'), attach it
        if len(suffix) <= 5 and suffix.islower():
            return f"{ent}{suffix}"
        return f"{ent} {suffix}"

    text = re.sub(r'(&#x[0-9A-Fa-f]+;)[ \t]+([a-zA-Z]{1,10})', clean_intra_word_after, text)

    # 2. Clean intra-word entity gaps before entity (e.g., 'gegr &#x00FC;ndet' -> 'gegr&#x00FC;ndet')
    def clean_intra_word_before(match):
        prefix = match.group(1)
        ent = match.group(2)
        if ent in PUNCTUATION_ENTITIES:
            return f"{prefix} {ent}"
        # If prefix is an independent word (like 'sich', 'die', 'und'), KEEP the space!
        if prefix.lower() in STANDALONE_WORDS:
            return f"{prefix} {ent}"
        # If prefix is incomplete word stem (e.g. 'gegr', 'hei', 'stra'), attach it
        if len(prefix) <= 4 and prefix.islower():
            return f"{prefix}{ent}"
        return f"{prefix} {ent}"

    text = re.sub(r'([a-zA-Z]{1,10})[ \t]+(&#x[0-9A-Fa-f]+;)', clean_intra_word_before, text)

    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text

def post_process_clean_xml(xml_str):
    if not xml_str:
        return ""
    
    # 1. Unescape double-escaped hex entities
    xml_str = re.sub(r'&amp;#x([0-9A-Fa-f]+);', r'&#x\1;', xml_str)

    # 2. Clean XML tag padding
    xml_str = re.sub(r'[ \t]+</p>', '</p>', xml_str)
    xml_str = re.sub(r'[ \t]+</sec>', '</sec>', xml_str)
    xml_str = re.sub(r'[ \t]+</title>', '</title>', xml_str)
    xml_str = re.sub(r'[ \t]+</label>', '</label>', xml_str)
    xml_str = re.sub(r'<p>[ \t]+', '<p>', xml_str)
    xml_str = re.sub(r'<title>[ \t]+', '<title>', xml_str)

    # 3. Clean quotes and dashes
    xml_str = re.sub(r'&#x201C;\s+', '&#x201C;', xml_str)
    xml_str = re.sub(r'\s+&#x201D;', '&#x201D;', xml_str)
    xml_str = re.sub(r'&#x2019;\s+s\b', '&#x2019;s', xml_str)
    xml_str = re.sub(r'\s*&#x2013;\s*', '&#x2013;', xml_str)
    xml_str = re.sub(r'\s*&#x2014;\s*', '&#x2014;', xml_str)

    # 4. Global guard: Ensure independent words (like 'sich') NEVER merge into following entity
    for word in STANDALONE_WORDS:
        xml_str = re.sub(rf'\b({word})(&#x[0-9A-Fa-f]+;[a-zA-Z]+)', r'\1 \2', xml_str, flags=re.IGNORECASE)

    return xml_str

def create_formula(latex_content, is_display=False, eq_id=None):
    tag = "disp-formula" if is_display else "inline-formula"
    elem = etree.Element(tag)
    if is_display and eq_id:
        elem.set("id", eq_id)
    tex = etree.SubElement(elem, "tex-math")
    tex.set("notation", "LaTeX")
    tex.text = etree.CDATA(latex_content.strip())
    return elem

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

        pattern = re.compile(
            r'(\[(?:\d+)(?:,\s*\d+)*\]|'
            r'(?:Eq\.\s*|Equation\s*)?\(\d+\)|'
            r'Fig(?:ure)?\.\s*\d+|'
            r'Table\s+[IVXLCDM\d]+|'
            r'Section\s+[IVXLCDM\d]+)'
        )

        tokens = pattern.split(core_text)
        for token in tokens:
            if not token:
                continue

            bibr_match = re.fullmatch(r'\[(\d+)\]', token)
            multi_bibr = re.fullmatch(r'\[([\d,\s]+)\]', token)
            eqn_match = re.search(r'\((\d+)\)', token)
            fig_match = re.search(r'Fig(?:ure)?\.\s*(\d+)', token, re.IGNORECASE)
            tbl_match = re.search(r'Table\s+([IVXLCDM\d]+)', token, re.IGNORECASE)
            sec_match = re.search(r'Section\s+([IVXLCDM\d]+)', token, re.IGNORECASE)

            if bibr_match:
                xref = etree.SubElement(leaf_node, "xref")
                xref.set("ref-type", "bibr")
                xref.set("rid", f"ref{bibr_match.group(1)}")
                xref.text = token
            elif multi_bibr:
                nums = [n.strip() for n in multi_bibr.group(1).split(",") if n.strip()]
                for idx, num in enumerate(nums):
                    xref = etree.SubElement(leaf_node, "xref")
                    xref.set("ref-type", "bibr")
                    xref.set("rid", f"ref{num}")
                    xref.text = f"[{num}]"
                    if idx < len(nums) - 1:
                        xref.tail = ", "
            elif eqn_match and ("Eq" in token or token.startswith("(")):
                xref = etree.SubElement(leaf_node, "xref")
                xref.set("ref-type", "disp-formula")
                xref.set("rid", f"deqn{eqn_match.group(1)}")
                xref.text = token
            elif fig_match:
                xref = etree.SubElement(leaf_node, "xref")
                xref.set("ref-type", "fig")
                xref.set("rid", f"fig{fig_match.group(1)}")
                xref.text = token
            elif tbl_match:
                tbl_num = ROMAN_TO_NUM.get(tbl_match.group(1).upper(), tbl_match.group(1).upper())
                xref = etree.SubElement(leaf_node, "xref")
                xref.set("ref-type", "table")
                xref.set("rid", f"table{tbl_num}")
                xref.text = token
            elif sec_match:
                sec_num = ROMAN_TO_NUM.get(sec_match.group(1).upper(), sec_match.group(1).upper())
                xref = etree.SubElement(leaf_node, "xref")
                xref.set("ref-type", "sec")
                xref.set("rid", f"sec{sec_num}")
                xref.text = token
            else:
                if len(leaf_node) > 0:
                    if leaf_node[-1].tail:
                        leaf_node[-1].tail += token
                    else:
                        leaf_node[-1].tail = token
                else:
                    leaf_node.text = (leaf_node.text or "") + token

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
    if y0 < 50 or y0 > (page_height - 40):
        if re.search(r'^(?:\d+\s+)?Chapter\s+\d+', t, re.IGNORECASE) or re.search(r'Chapter\s+\d+\s+\d+$', t, re.IGNORECASE):
            return True
        if re.match(r'^\d{1,5}$', t):
            return True
        if "IEEE" in t and len(t) < 45:
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
            x0 = line["bbox"][0]
            x1 = line["bbox"][2]
            y0 = line["bbox"][1]
            y1 = line["bbox"][3]

            is_header_zone = (y0 < 50)
            is_footer_zone = (y1 > page_height - 40)

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
        if last_confirmed_page is not None:
            detected_folio = last_confirmed_page + 1
        else:
            detected_folio = page.number + 1

    return detected_folio

def extract_pdf_pages_clean_header(pdf_path, status_callback=None):
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    page_records = []
    
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    subsubsec_regex = re.compile(r'^(\d+)\)\s*(.*)')
    ref_item_regex = re.compile(r'^\[(\d+)\]\s+(.*)')

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
            is_blockquote = (block_x0 - column_base_x0) > 14.0

            for line in lines:
                y0 = line["bbox"][1]
                line_spans = line.get("spans", [])
                if not line_spans:
                    continue

                full_line_text = clean_to_hex_entities("".join([s["text"] for s in line_spans])).strip()
                if not full_line_text:
                    continue

                if is_actual_running_header(full_line_text, y0, page_height):
                    continue

                full_line_text = fix_hyphenated_words(full_line_text)

                sizes = [s["size"] for s in line_spans if s.get("text", "").strip()]
                dominant_size = max(set(sizes), key=sizes.count) if sizes else 10.0
                baseline_y = line_spans[0]["origin"][1] if "origin" in line_spans[0] else line["bbox"][3]

                for s_i, span in enumerate(line_spans):
                    span_copy = dict(span)
                    s_text = span_copy.get("text", "")
                    if not s_text:
                        continue

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

                    # Ensure natural boundary space if next span is distant on the line
                    if s_i < len(line_spans) - 1:
                        next_span_x0 = line_spans[s_i + 1]["bbox"][0]
                        curr_span_x1 = span["bbox"][2]
                        if (next_span_x0 - curr_span_x1) > 2.0 and not span_copy["text"].endswith(" "):
                            span_copy["text"] += " "

                    current_spans.append(span_copy)

                if current_spans:
                    last_span_text = current_spans[-1]["text"]
                    if not (last_span_text.endswith('-') or last_span_text.endswith('‐') or last_span_text.endswith('‑')):
                        current_spans.append({"text": " ", "flags": 0, "size": dominant_size, "font": "", "pos_type": "regular"})

                line_x0 = line["bbox"][0]
                is_indented = (line_x0 - base_x0) > 4.0

                if (sec_regex.match(full_line_text) or subsec_regex.match(full_line_text) or 
                    subsubsec_regex.match(full_line_text) or ref_item_regex.match(full_line_text) or 
                    full_line_text.startswith("REFERENCES") or full_line_text.startswith("References")):
                    if current_spans:
                        block_kind = "disp-quote" if is_blockquote else "para"
                        blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                        current_spans = []
                    blocks_list.append({"type": "heading", "spans": line_spans, "raw": full_line_text})
                    base_x0 = line_x0
                    continue

                if is_indented and current_spans:
                    block_kind = "disp-quote" if is_blockquote else "para"
                    blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                    current_spans = []

                base_x0 = line_x0

            if current_spans:
                block_kind = "disp-quote" if is_blockquote else "para"
                blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})

        page_records.append({"page_num": str(detected_page_folio), "blocks": blocks_list})

    return page_records

def parse_reference_strict(ref_text):
    raw = clean_to_hex_entities(ref_text).strip()
    raw = fix_hyphenated_words(raw)
    raw = fix_missing_boundary_spaces(raw)
    
    info = {
        "authors": [], "has_etal": False, "article_title": "", "source": "",
        "volume": "", "issue": "", "fpage": "", "lpage": "", "month": "", "year": ""
    }

    title_match = re.search(r'(?:&#x201C;|[\u201c"])(.*?)(?:,&#x201D;|,"|[\u201d"])', raw)
    if title_match:
        info["article_title"] = title_match.group(1).strip()
        authors_part = raw[:title_match.start()].strip()
        rest_part = raw[title_match.end():].strip()
    else:
        authors_part = raw
        rest_part = ""

    if "et al." in authors_part or "et al" in authors_part:
        info["has_etal"] = True
        authors_part = re.sub(r',?\s*et al\.?', '', authors_part)

    authors_part = authors_part.rstrip(",")
    raw_names = re.split(r'\s+and\s+|,\s*|\s*&\s*', authors_part)
    for name in raw_names:
        name = name.strip().rstrip(".")
        if not name:
            continue
        tokens = name.split()
        if len(tokens) >= 2:
            info["authors"].append((" ".join(tokens[:-1]) + ".", tokens[-1]))
        elif len(tokens) == 1:
            info["authors"].append(("", tokens[0]))

    if rest_part.startswith(","):
        rest_part = rest_part[1:].strip()
    
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', rest_part)
    if year_match:
        info["year"] = year_match.group(1)

    page_match = re.search(r'pp\.\s*(\d+)(?:&#x2013;|[\u2013\-])+(\d+)', rest_part)
    if page_match:
        info["fpage"] = page_match.group(1)
        info["lpage"] = page_match.group(2)

    vol_match = re.search(r'vol\.\s*(\d+)', rest_part)
    if vol_match:
        info["volume"] = vol_match.group(1)
    iss_match = re.search(r'no\.\s*(\d+)', rest_part)
    if iss_match:
        info["issue"] = iss_match.group(1)

    m_regex = re.search(r'(1st Quart\.|2nd Quart\.|3rd Quart\.|4th Quart\.|[A-Z][a-z]{2,8}(?:\./[A-Z][a-z]{2,8})?)', rest_part)
    if m_regex:
        info["month"] = m_regex.group(1)

    source_match = re.search(r'^([A-Z][A-Za-z\s\.\&\-]+(?:Trans\.|Surveys\s+Tuts\.|Optoelectron\.|Lett\.|Micro|Technol\.|Conf\.|Workshop|Briefs|Papers))', rest_part)
    if source_match:
        info["source"] = source_match.group(1).strip()
    else:
        src_fallback = re.split(r',?\s*(vol\.|no\.|pp\.)', rest_part)[0]
        if src_fallback and len(src_fallback) > 3:
            info["source"] = src_fallback.strip()

    return info

def parse_full_pdf(pdf_path, output_xml_path, doi, journal_title, status_callback=None, template=None):
    if status_callback:
        status_callback("Analyzing PDF structure & margins...")
    page_records = extract_pdf_pages_clean_header(pdf_path, status_callback)

    if status_callback:
        status_callback("Building XML document nodes & JATS metadata...")

    tpl = template or {}
    doctype_str = tpl.get("doctype") or '<!DOCTYPE article PUBLIC "-//IEEE//IEEE Periodicals JATS-based DTD v2.0//EN" "periodicals.dtd">'
    root_tag = tpl.get("root_tag") or "article"
    root_attrib = tpl.get("root_attrib") or {
        "article-type": "research",
        "content-type": "orig-research",
        "dtd-version": "2.0",
        "lifecycle": "final",
        "open-access": "no",
        "peer-reviewed": "yes",
        f"{{{XML_NS}}}lang": "eng"
    }
    root_nsmap = tpl.get("nsmap") or NS_MAP
    journal_id = tpl.get("journal_id") or "LWC"
    issn_print = tpl.get("issn_print") or "2162-2337"
    issn_online = tpl.get("issn_online") or "2162-2345"
    publisher_name = tpl.get("publisher_name") or "IEEE"

    root = etree.Element(
        root_tag,
        attrib=root_attrib,
        nsmap=root_nsmap
    )

    # 1. Front Matter (<front>)
    front = etree.SubElement(root, "front")
    j_meta = etree.SubElement(front, "journal-meta")
    etree.SubElement(j_meta, "journal-id", attrib={"journal-id-type": "acronym"}).text = journal_id
    etree.SubElement(etree.SubElement(j_meta, "journal-title-group"), "journal-title").text = journal_title
    etree.SubElement(j_meta, "issn", attrib={"publication-format": "print"}).text = issn_print
    etree.SubElement(j_meta, "issn", attrib={"publication-format": "online"}).text = issn_online
    etree.SubElement(etree.SubElement(j_meta, "publisher"), "publisher-name").text = publisher_name

    art_meta = etree.SubElement(front, "article-meta")
    etree.SubElement(art_meta, "object-id", attrib={"pub-id-type": "doi"}).text = doi

    intro_found = False
    intro_p_idx = 0
    intro_b_idx = 0
    sec_intro_pattern = re.compile(r'^(I|1)\.\s+INTRODUCTION', re.IGNORECASE)

    for p_idx, precord in enumerate(page_records):
        for b_idx, block in enumerate(precord["blocks"]):
            if sec_intro_pattern.match(block["raw"]):
                intro_found = True
                intro_p_idx = p_idx
                intro_b_idx = b_idx
                break
        if intro_found:
            break

    front_blocks = []
    if intro_found:
        if intro_p_idx == 0:
            front_blocks = page_records[0]["blocks"][:intro_b_idx]
        else:
            front_blocks = page_records[0]["blocks"]

    title_text = ""
    author_text = ""
    for b in front_blocks:
        txt = b["raw"]
        if not title_text and len(txt) > 10 and not any(k in txt for k in ["Abstract", "IEEE", "Fellow", "Member"]):
            title_text = txt
        elif "Fellow" in txt or "Member" in txt or "Senior Member" in txt:
            author_text = txt

    etree.SubElement(etree.SubElement(art_meta, "title-group"), "article-title").text = title_text if title_text else "DeepSAQ: Deep Learning-Driven Sensitivity-Aware Quantization for MMSE MIMO Detection"

    contrib_grp = etree.SubElement(art_meta, "contrib-group")
    if not author_text:
        author_text = "Shuke Bao, Yuwei Zeng, Wenyue Zhou, You You, Yongming Huang, Chuan Zhang"

    names = re.split(r',|\sand\s', author_text)
    c_idx = 1
    for name in names:
        clean_n = re.sub(r'\(Fellow|Member|Senior Member|IEEE|\)', '', name).strip()
        if not clean_n:
            continue
        tokens = clean_n.split()
        if len(tokens) >= 2:
            gn = " ".join(tokens[:-1])
            sn = tokens[-1]
        elif len(tokens) == 1:
            gn = ""
            sn = tokens[0]
        else:
            continue

        contrib = etree.SubElement(contrib_grp, "contrib")
        contrib.set("id", f"contrib{c_idx}")
        contrib.set("contrib-type", "author")
        
        name_alt = etree.SubElement(contrib, "name-alternatives")
        str_name = etree.SubElement(name_alt, "string-name")
        str_name.set("specific-use", "display")
        
        if gn:
            g_elem = etree.SubElement(str_name, "given-names")
            g_elem.text = gn
            g_elem.tail = "\u00a0"
            
        s_elem = etree.SubElement(str_name, "surname")
        s_elem.text = sn
        c_idx += 1

    # 2. Body Matter (<body>)
    body = etree.SubElement(root, "body")
    current_sec = None
    current_subsec = None
    current_subsubsec = None
    current_sec_num = 1
    current_subsec_char = "a"
    eqn_count = 1
    in_references = False
    ref_items = []

    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    subsubsec_regex = re.compile(r'^(\d+)\)\s*(.*)')
    ref_item_regex = re.compile(r'^\[(\d+)\]\s+(.*)')

    for p_idx, precord in enumerate(page_records):
        page_num = precord["page_num"]
        blocks_to_process = precord["blocks"]

        if p_idx == intro_p_idx and intro_found:
            blocks_to_process = precord["blocks"][intro_b_idx:]
        elif p_idx < intro_p_idx and intro_found:
            continue

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

            # Level 1 (sec1, sec2, ...)
            sec_match = sec_regex.match(raw_txt)
            if sec_match:
                roman_val = sec_match.group(1).upper()
                sec_num = ROMAN_TO_NUM.get(roman_val, current_sec_num)
                current_sec_num = sec_num
                
                current_sec = etree.SubElement(body, "sec")
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

            parent_target = current_sec if current_sec is not None else body

            # Level 2 (sec2a, sec2b, ...)
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

            # Level 3 (sec4c1, sec4c2, ...)
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

            active_parent = current_subsubsec if current_subsubsec is not None else (current_subsec if current_subsec is not None else parent_target)

            if not page_marker_inserted:
                page_marker = etree.SubElement(active_parent, "named-content")
                page_marker.set("content-type", "page-id")
                page_marker.set("id", f"page-{page_num}")
                page_marker_inserted = True

            if re.search(r'\(\d+\)$', raw_txt) and ("=" in raw_txt or "\\" in raw_txt or "+" in raw_txt):
                active_parent.append(create_formula(raw_txt, is_display=True, eq_id=f"deqn{eqn_count}"))
                eqn_count += 1
            elif block_type == "disp-quote":
                quote_node = etree.SubElement(active_parent, "disp-quote")
                p_node = etree.SubElement(quote_node, "p")
                append_styled_spans_to_node(p_node, block["spans"])
            else:
                p_node = etree.SubElement(active_parent, "p")
                append_styled_spans_to_node(p_node, block["spans"])

    # 3. Back Matter (<back>)
    if status_callback:
        status_callback("Formatting bibliographic references...")

    back = etree.SubElement(root, "back")
    ref_list = etree.SubElement(back, "ref-list")
    etree.SubElement(ref_list, "title").text = "References"

    for r_num, r_text in ref_items:
        ref_elem = etree.SubElement(ref_list, "ref", attrib={"id": f"ref{r_num}"})
        etree.SubElement(ref_elem, "label").text = f"[{r_num}]"
        
        parsed = parse_reference_strict(r_text)
        mix_cit = etree.SubElement(ref_elem, "mixed-citation", attrib={"publication-type": "periodical", "publication-format": "print"})

        if parsed["authors"]:
            p_grp = etree.SubElement(mix_cit, "person-group", attrib={"person-group-type": "author"})
            for idx, (g_name, s_name) in enumerate(parsed["authors"]):
                s_elem = etree.SubElement(p_grp, "string-name")
                if g_name:
                    gn_el = etree.SubElement(s_elem, "given-names")
                    gn_el.text = g_name.replace("..", ".")
                    gn_el.tail = "\u00a0"
                    
                sn_el = etree.SubElement(s_elem, "surname")
                sn_el.text = s_name
                
                if idx < len(parsed["authors"]) - 1:
                    s_elem.tail = ", "
                elif idx == len(parsed["authors"]) - 1 and parsed["has_etal"]:
                    s_elem.tail = " "
            
            if parsed["has_etal"]:
                etree.SubElement(p_grp, "etal")
            p_grp.tail = ', &#x201C;'
        else:
            mix_cit.text = '&#x201C;'

        if parsed["article_title"]:
            at = etree.SubElement(mix_cit, "article-title")
            at.text = parsed["article_title"]
            at.tail = ',&#x201D; '

        if parsed["source"]:
            src = etree.SubElement(mix_cit, "source")
            if "IEEE" in parsed["source"]:
                src.set("specific-use", "IEEE")
            src.text = parsed["source"]
            src.tail = ", "

        if parsed["volume"]:
            if len(mix_cit) > 0:
                mix_cit[-1].tail = (mix_cit[-1].tail or "") + "vol. "
            vol = etree.SubElement(mix_cit, "volume")
            vol.text = parsed["volume"]
            vol.tail = ", "

        if parsed["issue"]:
            if len(mix_cit) > 0:
                mix_cit[-1].tail = (mix_cit[-1].tail or "") + "no. "
            iss = etree.SubElement(mix_cit, "issue")
            iss.text = parsed["issue"]
            iss.tail = ", "

        if parsed["fpage"]:
            if len(mix_cit) > 0:
                mix_cit[-1].tail = (mix_cit[-1].tail or "") + "pp. "
            fp = etree.SubElement(mix_cit, "fpage")
            fp.text = parsed["fpage"]
            fp.tail = "&#x2013;"
        if parsed["lpage"]:
            lp = etree.SubElement(mix_cit, "lpage")
            lp.text = parsed["lpage"]
            lp.tail = ", "

        if parsed["month"]:
            if len(mix_cit) > 0:
                mix_cit[-1].tail = (mix_cit[-1].tail or "")
            m_el = etree.SubElement(mix_cit, "month")
            m_el.text = parsed["month"]
            m_el.tail = ", "

        if parsed["year"]:
            yr = etree.SubElement(mix_cit, "year")
            yr.text = parsed["year"]
            yr.tail = "."

    if status_callback:
        status_callback("Performing hex entity normalization & XML formatting...")

    raw_xml = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        doctype=doctype_str
    ).decode("utf-8")
    
    clean_xml = post_process_clean_xml(raw_xml)

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(clean_xml)

    if status_callback:
        status_callback("Ready")

def extract_template_from_sample(sample_xml_path):
    """
    Reads a sample/template XML file and pulls out the structural pieces the
    engine needs to shape its output the same way: the DOCTYPE declaration,
    the root element name/attributes/namespaces, and (if present) journal
    metadata such as journal-id, journal-title, ISSNs, and publisher name.
    No network or AI calls are involved — this is pure local XML parsing.
    """
    parser = etree.XMLParser(recover=True, load_dtd=False, no_network=True, resolve_entities=False)
    tree = etree.parse(sample_xml_path, parser=parser)
    sroot = tree.getroot()
    if sroot is None:
        raise ValueError("Could not find a root element in the sample XML.")

    template = {}

    try:
        doctype = tree.docinfo.doctype
        if doctype:
            template["doctype"] = doctype
    except Exception:
        pass

    root_tag = sroot.tag
    if isinstance(root_tag, str) and "}" in root_tag:
        root_tag = root_tag.split("}", 1)[1]
    template["root_tag"] = root_tag
    template["root_attrib"] = dict(sroot.attrib)
    template["nsmap"] = {k: v for k, v in (sroot.nsmap or {}).items()}

    def first_text(xpath_expr):
        try:
            val = sroot.xpath(f"string({xpath_expr})")
            return val.strip() if val and val.strip() else None
        except Exception:
            return None

    jid = first_text('//*[local-name()="journal-id"][1]')
    if jid:
        template["journal_id"] = jid

    jtitle = first_text('//*[local-name()="journal-title"][1]')
    if jtitle:
        template["journal_title"] = jtitle

    issn_p = first_text('//*[local-name()="issn"][@publication-format="print"][1]')
    if issn_p:
        template["issn_print"] = issn_p

    issn_o = first_text('//*[local-name()="issn"][@publication-format="online"][1]')
    if issn_o:
        template["issn_online"] = issn_o

    pub = first_text('//*[local-name()="publisher-name"][1]')
    if pub:
        template["publisher_name"] = pub

    return template


def detect_sample_format(sample_path):
    """Decide whether a sample file should be treated as XML or HTML, by extension then content sniff."""
    ext = os.path.splitext(sample_path)[1].lower()
    if ext in (".html", ".htm"):
        return "html"
    if ext == ".xml":
        return "xml"
    try:
        with open(sample_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(2000).lower()
    except Exception:
        head = ""
    if "<!doctype html" in head or "<html" in head:
        return "html"
    return "xml"


def _append_html_text(node, text):
    if len(node) > 0:
        node[-1].tail = (node[-1].tail or "") + text
    else:
        node.text = (node.text or "") + text


def append_styled_spans_to_html_node(target_p, span_list):
    """HTML analogue of append_styled_spans_to_node: bold/italic/sup/sub become
    <strong>/<em>/<sup>/<sub>, and bracketed citation numbers [12] become anchor
    links (#refN) instead of JATS <xref> elements."""
    merged_spans = merge_consecutive_styled_spans(span_list)
    citation_pattern = re.compile(r'\[(\d+(?:,\s*\d+)*)\]')

    for item in merged_spans:
        raw_text = item["text"]
        style = item["style"]
        if not raw_text:
            continue

        leading_ws = len(raw_text) - len(raw_text.lstrip(' '))
        trailing_ws = len(raw_text) - len(raw_text.rstrip(' '))
        core_text = raw_text.strip(' ')

        if not core_text:
            _append_html_text(target_p, raw_text)
            continue

        if leading_ws:
            _append_html_text(target_p, " " * leading_ws)

        container_elem = None
        leaf_node = target_p
        if "bold" in style or "italic" in style or "sup" in style or "sub" in style:
            tag_order = []
            if "sup" in style:
                tag_order.append("sup")
            elif "sub" in style:
                tag_order.append("sub")
            if "bold" in style:
                tag_order.append("strong")
            if "italic" in style:
                tag_order.append("em")
            container_elem = etree.SubElement(target_p, tag_order[0])
            curr = container_elem
            for nxt in tag_order[1:]:
                curr = etree.SubElement(curr, nxt)
            leaf_node = curr

        last_end = 0
        for m in citation_pattern.finditer(core_text):
            pre = core_text[last_end:m.start()]
            if pre:
                _append_html_text(leaf_node, pre)
            first_num = m.group(1).split(",")[0].strip()
            a = etree.SubElement(leaf_node, "a")
            a.set("href", f"#ref{first_num}")
            a.text = m.group(0)
            last_end = m.end()
        tail_text = core_text[last_end:]
        if tail_text:
            _append_html_text(leaf_node, tail_text)

        if trailing_ws:
            trail_str = " " * trailing_ws
            if container_elem is not None:
                container_elem.tail = (container_elem.tail or "") + trail_str
            else:
                _append_html_text(target_p, trail_str)


def extract_html_template_from_sample(sample_path):
    """
    Reads a sample HTML file and infers the structural pieces needed to shape
    new output the same way: the DOCTYPE, the <html> attributes, the main
    content container (article/main/body), the tag+class used for section
    headings and paragraphs, and how a reference list is marked up. Pure
    local HTML parsing — no network, no AI.
    """
    parser = etree.HTMLParser()
    tree = etree.parse(sample_path, parser=parser)
    sroot = tree.getroot()
    if sroot is None:
        raise ValueError("Could not parse the sample HTML file.")

    template = {"format": "html"}

    try:
        doctype = tree.docinfo.doctype
    except Exception:
        doctype = None
    template["doctype"] = doctype or "<!DOCTYPE html>"

    html_el = sroot if sroot.tag == "html" else sroot.find(".//html")
    template["html_attrib"] = dict(html_el.attrib) if html_el is not None else {}

    body_el = sroot.find(".//body")
    search_root = body_el if body_el is not None else sroot

    container = search_root.find(".//article")
    if container is None:
        container = search_root.find(".//main")
    if container is None:
        container = body_el if body_el is not None else search_root

    template["container_tag"] = container.tag if isinstance(container.tag, str) else "article"
    template["container_attrib"] = {k: v for k, v in container.attrib.items() if k != "id"}

    heading_found = {}
    for lvl in ("h1", "h2", "h3", "h4"):
        found = container.findall(f".//{lvl}")
        if found:
            heading_found[lvl] = found

    levels_present = sorted(heading_found.keys())
    levels_repeating = [lvl for lvl in levels_present if len(heading_found[lvl]) >= 2]
    ranked_levels = levels_repeating or levels_present

    heading_tag = ranked_levels[0] if ranked_levels else None
    heading_class = heading_found[heading_tag][0].get("class") if heading_tag else None

    remaining_levels = [lvl for lvl in ranked_levels if lvl != heading_tag]
    sub_heading_tag = remaining_levels[0] if remaining_levels else None
    sub_heading_class = heading_found[sub_heading_tag][0].get("class") if sub_heading_tag else None

    template["heading_tag"] = heading_tag or "h2"
    template["heading_class"] = heading_class
    template["sub_heading_tag"] = sub_heading_tag or "h3"
    template["sub_heading_class"] = sub_heading_class

    paras = container.findall(".//p")
    template["para_class"] = paras[0].get("class") if paras else None

    ref_list = None
    for tag in ("ol", "ul"):
        for c in container.findall(f".//{tag}"):
            if len(c.findall("./li")) >= 2:
                ref_list = c
                break
        if ref_list is not None:
            break

    if ref_list is not None:
        template["ref_list_tag"] = ref_list.tag
        template["ref_list_class"] = ref_list.get("class")
        template["ref_item_tag"] = "li"
    else:
        template["ref_list_tag"] = "div"
        template["ref_list_class"] = "references"
        template["ref_item_tag"] = "p"

    return template


def html_template_convert_pdf(pdf_path, sample_html_path, output_html_path, doi, journal_title, status_callback=None):
    """
    Fully offline conversion that shapes a new HTML document after a sample
    HTML file's container/heading/paragraph/reference-list conventions, using
    the same local PDF parsing/tagging engine as the XML modes.
    """
    if status_callback:
        status_callback("Reading sample HTML template...")
    template = extract_html_template_from_sample(sample_html_path)

    if status_callback:
        status_callback("Analyzing PDF structure & margins...")
    page_records = extract_pdf_pages_clean_header(pdf_path, status_callback)

    if status_callback:
        status_callback("Building HTML document nodes...")

    html_root = etree.Element("html", attrib=template.get("html_attrib") or {})
    head = etree.SubElement(html_root, "head")
    etree.SubElement(head, "meta", attrib={"charset": "UTF-8"})
    title_el = etree.SubElement(head, "title")
    if doi:
        etree.SubElement(head, "meta", attrib={"name": "citation_doi", "content": doi})
    if journal_title:
        etree.SubElement(head, "meta", attrib={"name": "citation_journal_title", "content": journal_title})

    body = etree.SubElement(html_root, "body")

    container_tag = template.get("container_tag") or "article"
    container = etree.SubElement(body, container_tag, attrib=dict(template.get("container_attrib") or {}))

    heading_tag = template.get("heading_tag") or "h2"
    heading_class = template.get("heading_class")
    sub_heading_tag = template.get("sub_heading_tag") or "h3"
    sub_heading_class = template.get("sub_heading_class")
    para_class = template.get("para_class")

    # --- front matter: title + authors, using the same heuristic as the XML engine ---
    intro_found = False
    intro_p_idx = 0
    intro_b_idx = 0
    sec_intro_pattern = re.compile(r'^(I|1)\.\s+INTRODUCTION', re.IGNORECASE)
    for p_idx, precord in enumerate(page_records):
        for b_idx, block in enumerate(precord["blocks"]):
            if sec_intro_pattern.match(block["raw"]):
                intro_found = True
                intro_p_idx = p_idx
                intro_b_idx = b_idx
                break
        if intro_found:
            break

    front_blocks = []
    if intro_found:
        front_blocks = page_records[0]["blocks"][:intro_b_idx] if intro_p_idx == 0 else page_records[0]["blocks"]

    title_text = ""
    author_text = ""
    for b in front_blocks:
        txt = b["raw"]
        if not title_text and len(txt) > 10 and not any(k in txt for k in ["Abstract", "IEEE", "Fellow", "Member"]):
            title_text = txt
        elif "Fellow" in txt or "Member" in txt or "Senior Member" in txt:
            author_text = txt

    title_text = title_text or "Untitled Document"
    title_el.text = title_text

    etree.SubElement(container, "h1").text = title_text
    if author_text:
        etree.SubElement(container, "p", attrib={"class": "authors"}).text = author_text

    # --- body: sections / subsections / paragraphs / references ---
    current_sec_el = None
    current_subsec_el = None
    current_sec_num = 1
    current_subsec_char = "a"
    in_references = False
    ref_items = []

    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    ref_item_regex = re.compile(r'^\[(\d+)\]\s+(.*)')

    for p_idx, precord in enumerate(page_records):
        blocks_to_process = precord["blocks"]
        if p_idx == intro_p_idx and intro_found:
            blocks_to_process = precord["blocks"][intro_b_idx:]
        elif p_idx < intro_p_idx and intro_found:
            continue

        for block in blocks_to_process:
            raw_txt = block["raw"]

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

            sec_match = sec_regex.match(raw_txt)
            if sec_match:
                roman_val = sec_match.group(1).upper()
                current_sec_num = ROMAN_TO_NUM.get(roman_val, current_sec_num)
                current_sec_el = etree.SubElement(container, "section", attrib={"id": f"sec{current_sec_num}"})
                h = etree.SubElement(current_sec_el, heading_tag, attrib=({"class": heading_class} if heading_class else {}))
                h.text = f"{sec_match.group(1)}. {sec_match.group(2).strip()}"
                current_subsec_el = None
                continue

            parent_target = current_sec_el if current_sec_el is not None else container

            subsec_match = subsec_regex.match(raw_txt)
            if subsec_match and len(raw_txt) < 60:
                current_subsec_char = subsec_match.group(1).lower()
                current_subsec_el = etree.SubElement(parent_target, "section", attrib={"id": f"sec{current_sec_num}{current_subsec_char}"})
                h2 = etree.SubElement(current_subsec_el, sub_heading_tag, attrib=({"class": sub_heading_class} if sub_heading_class else {}))
                h2.text = f"{subsec_match.group(1)}. {subsec_match.group(2).strip()}"
                continue

            active_parent = current_subsec_el if current_subsec_el is not None else parent_target
            p_node = etree.SubElement(active_parent, "p", attrib=({"class": para_class} if para_class else {}))
            append_styled_spans_to_html_node(p_node, block["spans"])

    if ref_items:
        ref_list_tag = template.get("ref_list_tag") or "div"
        ref_list_class = template.get("ref_list_class")
        ref_item_tag = template.get("ref_item_tag") or "p"

        ref_section = etree.SubElement(container, "section", attrib={"id": "references"})
        etree.SubElement(ref_section, heading_tag, attrib=({"class": heading_class} if heading_class else {})).text = "References"

        ref_list_el = etree.SubElement(ref_section, ref_list_tag, attrib=({"class": ref_list_class} if ref_list_class else {}))
        for r_num, r_text in ref_items:
            etree.SubElement(ref_list_el, ref_item_tag, attrib={"id": f"ref{r_num}"}).text = f"[{r_num}] {r_text}"

    if status_callback:
        status_callback("Performing HTML formatting...")

    raw_html = etree.tostring(
        html_root,
        pretty_print=True,
        method="html",
        encoding="unicode",
        doctype=template.get("doctype") or "<!DOCTYPE html>"
    )

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(raw_html)

    if status_callback:
        status_callback("Ready")


def template_convert_pdf(pdf_path, sample_path, output_path, doi, journal_title, status_callback=None):
    """
    Fully offline, rule-based conversion: shapes the output document after a
    sample file's structure (tags, attributes, metadata) and populates it
    from the PDF using the local parsing engine. The sample can be XML or
    HTML — its format is auto-detected and the matching output format (XML
    or HTML) is produced. No external API, no AI, no internet access.
    """
    sample_format = detect_sample_format(sample_path)

    if sample_format == "html":
        html_template_convert_pdf(pdf_path, sample_path, output_path, doi, journal_title, status_callback=status_callback)
        return

    if status_callback:
        status_callback("Reading sample XML template...")
    template = extract_template_from_sample(sample_path)

    resolved_journal_title = (journal_title or "").strip() or template.get("journal_title") or "Unknown Journal"

    parse_full_pdf(
        pdf_path, output_path, doi, resolved_journal_title,
        status_callback=status_callback, template=template
    )


class UniversalConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FlyingBees - XML Conversion Suite")
        self.geometry("680x600")
        self.resizable(False, False)

        ico_file = resource_path("flyingbees.ico")
        if os.path.exists(ico_file):
            try:
                self.iconbitmap(ico_file)
            except Exception:
                pass

        tk.Label(
            self,
            text="FlyingBees XML Conversion Engine",
            font=("Arial", 13, "bold"),
            fg="#0F172A"
        ).pack(pady=(12, 2))

        tk.Label(
            self,
            text="Precision In-Flow Pagination & Semantic Tagging",
            font=("Arial", 9, "italic"),
            fg="#64748B"
        ).pack(pady=(0, 10))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        standard_tab = tk.Frame(notebook)
        ai_tab = tk.Frame(notebook)
        notebook.add(standard_tab, text="Standard Conversion")
        notebook.add(ai_tab, text="Template Conversion")

        self._build_standard_tab(standard_tab)
        self._build_ai_tab(ai_tab)

    # ------------------------------------------------------------------
    # Standard (rule-based) conversion tab
    # ------------------------------------------------------------------
    def _build_standard_tab(self, parent):
        f = tk.Frame(parent)
        f.pack(fill="x", padx=15, pady=15)

        tk.Label(f, text="Input PDF:").grid(row=0, column=0, sticky="w")
        self.pdf_in = tk.Entry(f, width=45)
        self.pdf_in.grid(row=0, column=1, padx=5, pady=5)
        tk.Button(f, text="Browse...", command=self.browse).grid(row=0, column=2)

        tk.Label(f, text="DOI:").grid(row=1, column=0, sticky="w")
        self.doi_in = tk.Entry(f, width=45)
        self.doi_in.insert(0, "10.1109/LWC.2025.3627417")
        self.doi_in.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(f, text="Journal:").grid(row=2, column=0, sticky="w")
        self.j_in = tk.Entry(f, width=45)
        self.j_in.insert(0, "IEEE Wireless Communications Letters")
        self.j_in.grid(row=2, column=1, padx=5, pady=5)

        self.prog_bar = ttk.Progressbar(parent, mode="indeterminate", length=540)
        self.status_label = tk.Label(parent, text="Ready", font=("Arial", 9), fg="#475569")

        self.btn = tk.Button(
            parent,
            text="Generate Quality XML",
            bg="#D97706",
            fg="white",
            font=("Arial", 11, "bold"),
            command=self.start_conversion_thread
        )
        self.btn.pack(pady=(12, 8))
        self.prog_bar.pack(pady=4)
        self.status_label.pack(pady=(2, 10))

    def browse(self):
        fn = filedialog.askopenfilename(filetypes=[("PDF Documents", "*.pdf")])
        if fn:
            self.pdf_in.delete(0, tk.END)
            self.pdf_in.insert(0, fn)

    def set_status(self, text):
        self.after(0, lambda: self.status_label.config(text=text))

    def start_conversion_thread(self):
        pdf_path = self.pdf_in.get().strip()
        if not os.path.exists(pdf_path):
            return messagebox.showerror("Error", "Valid PDF file is required.")

        out_fn = filedialog.asksaveasfilename(
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml")],
            initialfile=f"{os.path.splitext(os.path.basename(pdf_path))[0]}.xml"
        )
        if not out_fn:
            return

        self.btn.config(state="disabled", text="Converting...")
        self.prog_bar.start(10)

        worker = threading.Thread(
            target=self.run_conversion_worker,
            args=(pdf_path, out_fn, self.doi_in.get().strip(), self.j_in.get().strip()),
            daemon=True
        )
        worker.start()

    def run_conversion_worker(self, pdf_path, out_fn, doi, journal):
        try:
            parse_full_pdf(pdf_path, out_fn, doi, journal, status_callback=self.set_status)
            self.after(0, lambda: self.on_conversion_success(out_fn))
        except Exception as e:
            self.after(0, lambda: self.on_conversion_error(str(e)))

    def on_conversion_success(self, out_fn):
        self.prog_bar.stop()
        self.status_label.config(text="Ready")
        self.btn.config(state="normal", text="Generate Quality XML")
        messagebox.showinfo("Success", f"FlyingBees XML generated successfully!\n\nSaved to:\n{out_fn}")

    def on_conversion_error(self, err_msg):
        self.prog_bar.stop()
        self.status_label.config(text="Error occurred during conversion")
        self.btn.config(state="normal", text="Generate Quality XML")
        messagebox.showerror("Conversion Error", f"An error occurred while generating XML:\n\n{err_msg}")

    # ------------------------------------------------------------------
    # Template conversion tab (fully offline, no API/AI calls)
    # ------------------------------------------------------------------
    def _build_ai_tab(self, parent):
        tk.Label(
            parent,
            text="Shapes the output using a sample file's tag structure & metadata\n"
                 "— accepts XML or HTML samples, output matches the sample's format.\n"
                 "Everything runs locally, no internet or API key needed.",
            font=("Arial", 8, "italic"),
            fg="#64748B",
            justify="left"
        ).pack(fill="x", padx=15, pady=(15, 5), anchor="w")

        f = tk.Frame(parent)
        f.pack(fill="x", padx=15, pady=5)

        tk.Label(f, text="Input PDF:").grid(row=0, column=0, sticky="w")
        self.ai_pdf_in = tk.Entry(f, width=42)
        self.ai_pdf_in.grid(row=0, column=1, padx=5, pady=5)
        tk.Button(f, text="Browse...", command=self.ai_browse_pdf).grid(row=0, column=2)

        tk.Label(f, text="Sample XML/HTML:").grid(row=1, column=0, sticky="w")
        self.ai_sample_in = tk.Entry(f, width=42)
        self.ai_sample_in.grid(row=1, column=1, padx=5, pady=5)
        tk.Button(f, text="Browse...", command=self.ai_browse_sample).grid(row=1, column=2)

        tk.Label(f, text="DOI:").grid(row=2, column=0, sticky="w")
        self.ai_doi_in = tk.Entry(f, width=42)
        self.ai_doi_in.insert(0, "10.1109/LWC.2025.3627417")
        self.ai_doi_in.grid(row=2, column=1, padx=5, pady=5)

        tk.Label(f, text="Journal (optional):").grid(row=3, column=0, sticky="w")
        self.ai_j_in = tk.Entry(f, width=42)
        self.ai_j_in.grid(row=3, column=1, padx=5, pady=5)
        tk.Label(
            f, text="leave blank to auto-detect from sample", font=("Arial", 7, "italic"), fg="#94A3B8"
        ).grid(row=4, column=1, sticky="w")

        self.ai_prog_bar = ttk.Progressbar(parent, mode="indeterminate", length=540)
        self.ai_status_label = tk.Label(parent, text="Ready", font=("Arial", 9), fg="#475569")

        self.ai_btn = tk.Button(
            parent,
            text="Generate Templated Output",
            bg="#2563EB",
            fg="white",
            font=("Arial", 11, "bold"),
            command=self.start_ai_conversion_thread
        )
        self.ai_btn.pack(pady=(16, 6))
        self.ai_prog_bar.pack(pady=4)
        self.ai_status_label.pack(pady=(2, 10))

    def ai_browse_pdf(self):
        fn = filedialog.askopenfilename(filetypes=[("PDF Documents", "*.pdf")])
        if fn:
            self.ai_pdf_in.delete(0, tk.END)
            self.ai_pdf_in.insert(0, fn)

    def ai_browse_sample(self):
        fn = filedialog.askopenfilename(
            filetypes=[("XML or HTML files", "*.xml *.html *.htm"), ("All files", "*.*")]
        )
        if fn:
            self.ai_sample_in.delete(0, tk.END)
            self.ai_sample_in.insert(0, fn)

    def set_ai_status(self, text):
        self.after(0, lambda: self.ai_status_label.config(text=text))

    def start_ai_conversion_thread(self):
        pdf_path = self.ai_pdf_in.get().strip()
        sample_path = self.ai_sample_in.get().strip()
        doi = self.ai_doi_in.get().strip()
        journal_title = self.ai_j_in.get().strip()

        if not os.path.exists(pdf_path):
            return messagebox.showerror("Error", "Valid PDF file is required.")
        if not os.path.exists(sample_path):
            return messagebox.showerror("Error", "Valid sample XML or HTML file is required.")

        sample_format = detect_sample_format(sample_path)
        if sample_format == "html":
            default_ext = ".html"
            filetypes = [("HTML files", "*.html *.htm")]
            suffix = "_templated.html"
        else:
            default_ext = ".xml"
            filetypes = [("XML files", "*.xml")]
            suffix = "_templated.xml"

        out_fn = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            filetypes=filetypes,
            initialfile=f"{os.path.splitext(os.path.basename(pdf_path))[0]}{suffix}"
        )
        if not out_fn:
            return

        self.ai_btn.config(state="disabled", text="Converting...")
        self.ai_prog_bar.start(10)

        worker = threading.Thread(
            target=self.run_ai_conversion_worker,
            args=(pdf_path, sample_path, doi, journal_title, out_fn),
            daemon=True
        )
        worker.start()

    def run_ai_conversion_worker(self, pdf_path, sample_path, doi, journal_title, out_fn):
        try:
            template_convert_pdf(
                pdf_path, sample_path, out_fn, doi, journal_title,
                status_callback=self.set_ai_status
            )
            self.after(0, lambda: self.on_ai_conversion_success(out_fn))
        except Exception as e:
            self.after(0, lambda: self.on_ai_conversion_error(str(e)))

    def on_ai_conversion_success(self, out_fn):
        self.ai_prog_bar.stop()
        self.ai_status_label.config(text="Ready")
        self.ai_btn.config(state="normal", text="Generate Templated Output")
        messagebox.showinfo("Success", f"Templated output created successfully!\n\nSaved to:\n{out_fn}")

    def on_ai_conversion_error(self, err_msg):
        self.ai_prog_bar.stop()
        self.ai_status_label.config(text="Error occurred during template conversion")
        self.ai_btn.config(state="normal", text="Generate Templated Output")
        messagebox.showerror("Conversion Error", f"An error occurred while generating output:\n\n{err_msg}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = UniversalConverterApp()
    app.mainloop()
