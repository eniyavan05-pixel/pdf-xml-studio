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
    "ry", "ty", "ly", "ant", "ent", "ate", "ated", "ator", "atory", "pion",
    "pions", "cally", "fic", "fically"
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
    "&#x2022;", "&#x2212;", "&#x2264;", "&#x2265;", "&#x2208;"
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

def fix_hyphenated_words(text):
    if not text:
        return ""

    text = text.replace('\u00ad', '').replace('\xad', '')

    def line_break_replacer(match):
        prefix = match.group(1)
        suffix = match.group(2)
        p_low = prefix.lower()
        s_low = suffix.lower()

        if p_low in NUMBER_PREFIXES or s_low in VALID_COMPOUND_WORDS:
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
    xml_str = re.sub(r'[ \t]+</p>', '</p>', xml_str)
    xml_str = re.sub(r'[ \t]+</sec>', '</sec>', xml_str)
    xml_str = re.sub(r'[ \t]+</title>', '</title>', xml_str)
    xml_str = re.sub(r'[ \t]+</label>', '</label>', xml_str)
    xml_str = re.sub(r'<p>[ \t]+', '<p>', xml_str)
    xml_str = re.sub(r'<title>[ \t]+', '<title>', xml_str)
    xml_str = re.sub(r'&#x201C;\s+', '&#x201C;', xml_str)
    xml_str = re.sub(r'\s+&#x201D;', '&#x201D;', xml_str)
    xml_str = re.sub(r'&#x2019;\s+s\b', '&#x2019;s', xml_str)
    xml_str = re.sub(r'\s*&#x2013;\s*', '&#x2013;', xml_str)
    xml_str = re.sub(r'\s*&#x2014;\s*', '&#x2014;', xml_str)

    xml_str = re.sub(r'<p>\s*</p>', '', xml_str)
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
        if "IEEE" in t and len(t) < 45:
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
            x0 = line["bbox"][0]
            x1 = line["bbox"][2]
            y0 = line["bbox"][1]
            y1 = line["bbox"][3]

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

            prev_line_y1 = None
            prev_line_height = 12.0

            for line in lines:
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
                dominant_size = max(set(sizes), key=sizes.count) if sizes else 10.0
                baseline_y = line_spans[0]["origin"][1] if "origin" in line_spans[0] else line["bbox"][3]

                line_x0 = line["bbox"][0]
                is_indented = (line_x0 - base_x0) > 4.0
                
                has_vertical_block_gap = False
                if prev_line_y1 is not None:
                    gap = y0 - prev_line_y1
                    if gap > (prev_line_height * 0.35):
                        has_vertical_block_gap = True

                is_speaker_dialogue = bool(SPEAKER_LABEL_REGEX.match(full_line_text))

                if (is_indented or has_vertical_block_gap or is_speaker_dialogue) and current_spans:
                    block_kind = "disp-quote" if is_blockquote else "para"
                    blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                    current_spans = []
                    base_x0 = line_x0

                raw_line_end = "".join([s.get("text", "") for s in line_spans]).rstrip()
                line_ends_with_hyphen = raw_line_end.endswith('-') or raw_line_end.endswith('‐') or raw_line_end.endswith('‑') or raw_line_end.endswith('\xad')

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

                if current_spans and not line_ends_with_hyphen:
                    if not current_spans[-1]["text"].endswith(" "):
                        current_spans.append({"text": " ", "flags": 0, "size": dominant_size, "font": "", "pos_type": "regular"})

                if (sec_regex.match(full_line_text) or subsec_regex.match(full_line_text) or 
                    subsubsec_regex.match(full_line_text) or ref_item_regex.match(full_line_text) or 
                    full_line_text.startswith("REFERENCES") or full_line_text.startswith("References")):
                    if current_spans:
                        block_kind = "disp-quote" if is_blockquote else "para"
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
                block_kind = "disp-quote" if is_blockquote else "para"
                blocks_list.append({"type": block_kind, "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})

        page_records.append({"page_num": str(detected_page_folio), "blocks": blocks_list})

    return page_records

def parse_full_pdf(pdf_path, output_xml_path, doi, journal_title, status_callback=None):
    if status_callback:
        status_callback("Analyzing PDF layout...")
    page_records = extract_pdf_pages_clean_header(pdf_path, status_callback)

    if status_callback:
        status_callback("Building XML document nodes...")

    root = etree.Element(
        "article",
        attrib={
            "article-type": "research",
            "content-type": "orig-research",
            "dtd-version": "2.0",
            "lifecycle": "final",
            "open-access": "no",
            "peer-reviewed": "yes",
            f"{{{XML_NS}}}lang": "eng"
        },
        nsmap=NS_MAP
    )

    # 1. Front Matter (<front>)
    front = etree.SubElement(root, "front")
    j_meta = etree.SubElement(front, "journal-meta")
    etree.SubElement(j_meta, "journal-id", attrib={"journal-id-type": "acronym"}).text = "LWC"
    etree.SubElement(etree.SubElement(j_meta, "journal-title-group"), "journal-title").text = journal_title
    etree.SubElement(j_meta, "issn", attrib={"publication-format": "print"}).text = "2162-2337"
    etree.SubElement(j_meta, "issn", attrib={"publication-format": "online"}).text = "2162-2345"
    etree.SubElement(etree.SubElement(j_meta, "publisher"), "publisher-name").text = "IEEE"

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

            if block_type == "disp-quote":
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

    if status_callback:
        status_callback("Performing hex entity normalization & XML formatting...")

    doctype = '<!DOCTYPE article PUBLIC "-//IEEE//IEEE Periodicals JATS-based DTD v2.0//EN" "periodicals.dtd">'
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

    if status_callback:
        status_callback("Ready")

class UniversalConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TTBS - XML Conversion Suite")
        self.geometry("640x410")
        self.resizable(False, False)

        ico_file = resource_path("flyingbees.ico")
        if os.path.exists(ico_file):
            try:
                self.iconbitmap(ico_file)
            except Exception:
                pass

        tk.Label(
            self,
            text="TTBS XML Conversion Engine",
            font=("Arial", 13, "bold"),
            fg="#0F172A"
        ).pack(pady=(12, 2))
        
        tk.Label(
            self,
            text="Precision In-Flow Pagination & Multi-Format Tagging",
            font=("Arial", 9, "italic"),
            fg="#64748B"
        ).pack(pady=(0, 10))

        f = tk.Frame(self)
        f.pack(fill="x", padx=25, pady=5)

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

        self.prog_bar = ttk.Progressbar(self, mode="indeterminate", length=540)
        self.status_label = tk.Label(self, text="Ready", font=("Arial", 9), fg="#475569")
        
        self.btn = tk.Button(
            self,
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

        self.btn.config(state="disabled", text="Converting XML...")
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
        messagebox.showinfo("Success", f"TTBS XML generated successfully!\n\nSaved to:\n{out_fn}")

    def on_conversion_error(self, err_msg):
        self.prog_bar.stop()
        self.status_label.config(text="Error occurred during conversion")
        self.btn.config(state="normal", text="Generate Quality XML")
        messagebox.showerror("Conversion Error", f"An error occurred while generating XML:\n\n{err_msg}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = UniversalConverterApp()
    app.mainloop()
