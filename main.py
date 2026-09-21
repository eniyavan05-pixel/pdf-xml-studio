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
    return re.sub(r'([a-zA-Z]{2,})[-‐‑]\s+([a-zA-Z]{2,})', line_break_replacer, text)

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
    return re.sub(r'[ \t]{2,}', ' ', text)

def post_process_clean_xml(xml_str):
    if not xml_str:
        return ""
    xml_str = re.sub(r'&amp;#x([0-9A-Fa-f]+);', r'&#x\1;', xml_str)
    xml_str = re.sub(r'&#x00A0;', ' ', xml_str)
    xml_str = xml_str.replace("’", "&#x2019;").replace("‘", "&#x2018;").replace("“", "&#x201C;").replace("”", "&#x201D;").replace("–", "&#x2013;").replace("—", "&#x2014;")
    xml_str = re.sub(r'[ \t]+</para>', '</para>', xml_str)
    xml_str = re.sub(r'[ \t]+</title>', '</title>', xml_str)
    xml_str = re.sub(r'<para>[ \t]+', '<para>', xml_str)
    xml_str = re.sub(r'<title>[ \t]+', '<title>', xml_str)
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

        leaf_node = target_elem
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
            leaf_node = curr_elem

        if len(leaf_node) > 0:
            if leaf_node[-1].tail:
                leaf_node[-1].tail += core_text
            else:
                leaf_node[-1].tail = core_text
        else:
            leaf_node.text = (leaf_node.text or "") + core_text

        if trailing_ws > 0:
            trail_str = " " * trailing_ws
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
    
    chapter_regex = re.compile(r'^(CHAPTER\s+\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|\d+)\b', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    figure_regex = re.compile(r'^(Figure\s+\d+(?:\.\d+)?)\b', re.IGNORECASE)
    note_regex = re.compile(r'^(\d+)\.\s+(.*)')
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
                is_fig = bool(figure_regex.match(full_line_text))
                is_note = bool(y0 > page_height - 120 and note_regex.match(full_line_text))
                is_caps_title = bool(
                    full_line_text.isupper() 
                    and any(c.isalpha() for c in full_line_text) 
                    and 3 < len(full_line_text) < 120 
                    and not full_line_text.endswith('.')
                    and max(sizes, default=0) >= dominant_size
                )

                if is_chap or is_sec or is_fig or is_note or is_caps_title:
                    if current_spans:
                        b_type = "blockquote" if is_blockquote else ("sidebar" if is_sidebar else "para")
                        blocks_list.append({
                            "type": b_type, 
                            "spans": current_spans, 
                            "raw": "".join([s["text"] for s in current_spans]).strip(),
                            "is_indented": is_indented
                        })
                        current_spans = []
                    
                    if is_chap:
                        kind = "chap_title"
                    elif is_fig:
                        kind = "figure"
                    elif is_note:
                        kind = "note"
                    else:
                        kind = "heading"

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

def parse_full_pdf(pdf_path, output_xml_path, doi, book_title, status_callback=None):
    if status_callback:
        status_callback("Analyzing PDF layout...")
    page_records = extract_pdf_pages_clean_header(pdf_path, status_callback)

    if status_callback:
        status_callback("Building DocBook XML document...")

    book_id = "b-" + re.sub(r'[^a-zA-Z0-9]', '', os.path.splitext(os.path.basename(output_xml_path))[0])
    id_counter = 1

    def next_id():
        nonlocal id_counter
        curr = f"{book_id}-{id_counter:07d}"
        id_counter += 1
        return curr

    root = etree.Element(
        f"{{{DOCBOOK_NS}}}book",
        attrib={
            "version": "5.0",
            f"{{{XML_NS}}}lang": "en",
            "role": "fullText",
            f"{{{XML_NS}}}id": book_id
        },
        nsmap=NS_MAP
    )

    # 1. Info Block
    info_elem = etree.SubElement(root, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}}id": next_id()})
    t_elem = etree.SubElement(info_elem, f"{{{DOCBOOK_NS}}}title", attrib={f"{{{XML_NS}}}id": next_id()})
    t_elem.text = clean_to_hex_entities(book_title)

    if doi:
        doi_elem = etree.SubElement(info_elem, f"{{{DOCBOOK_NS}}}biblioid", attrib={"class": "doi"})
        doi_elem.text = doi
        obj_id_elem = etree.SubElement(info_elem, f"{{{DOCBOOK_NS}}}object-id", attrib={"pub-id-type": "doi"})
        obj_id_elem.text = doi

    # 2. Front Matter Part Setup
    front_part = etree.SubElement(root, f"{{{DOCBOOK_NS}}}part", attrib={"role": "front", f"{{{XML_NS}}}id": next_id()})
    front_info = etree.SubElement(front_part, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}}id": next_id()})
    front_title = etree.SubElement(front_info, f"{{{DOCBOOK_NS}}}title", attrib={f"{{{XML_NS}}}id": next_id()})
    front_title.text = "Front matter"

    preface_elem = etree.SubElement(front_part, f"{{{DOCBOOK_NS}}}preface", attrib={"role": "prelims", f"{{{XML_NS}}}id": next_id()})
    toc_elem = etree.SubElement(front_part, f"{{{DOCBOOK_NS}}}toc", attrib={f"{{{XML_NS}}}id": next_id()})

    # 3. Content Assembly Loop
    current_chapter = None
    current_section = None
    chap_count = 0
    fig_count = 0
    prev_block_type = "chap_title"
    in_front_matter = True

    for precord in page_records:
        page_num = precord["page_num"]
        page_pi = etree.ProcessingInstruction("page", f'value="{page_num}"')
        page_pi_added = False

        for block in precord["blocks"]:
            raw_txt = block["raw"]
            b_type = block.get("type", "para")

            if b_type == "chap_title":
                in_front_matter = False
                chap_count += 1
                current_chapter = etree.SubElement(root, f"{{{DOCBOOK_NS}}}chapter", attrib={
                    "label": str(chap_count),
                    f"{{{XML_NS}}}id": f"{book_id}-chapter{chap_count}"
                })
                c_info = etree.SubElement(current_chapter, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}}id": next_id()})
                c_title = etree.SubElement(c_info, f"{{{DOCBOOK_NS}}}title", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    c_title.append(page_pi)
                    page_pi_added = True
                c_title.text = clean_to_hex_entities(raw_txt)
                current_section = None
                prev_block_type = "chap_title"
                continue

            if in_front_matter:
                target_container = preface_elem
                if "contents" in raw_txt.lower():
                    target_container = toc_elem
                p = etree.SubElement(target_container, f"{{{DOCBOOK_NS}}}para", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    p.append(page_pi)
                    page_pi_added = True
                append_styled_spans_to_node(p, block["spans"])
                continue

            if current_chapter is None:
                chap_count += 1
                current_chapter = etree.SubElement(root, f"{{{DOCBOOK_NS}}}chapter", attrib={
                    "label": str(chap_count),
                    f"{{{XML_NS}}}id": f"{book_id}-chapter{chap_count}"
                })
                c_info = etree.SubElement(current_chapter, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}}id": next_id()})
                c_title = etree.SubElement(c_info, f"{{{DOCBOOK_NS}}}title", attrib={f"{{{XML_NS}}}id": next_id()})
                c_title.text = f"Chapter {chap_count}"

            if b_type == "heading":
                current_section = etree.SubElement(current_chapter, f"{{{DOCBOOK_NS}}}section", attrib={f"{{{XML_NS}}}id": next_id()})
                s_info = etree.SubElement(current_section, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}}id": next_id()})
                s_title = etree.SubElement(s_info, f"{{{DOCBOOK_NS}}}title", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    s_title.append(page_pi)
                    page_pi_added = True
                formatted_title = smart_title_case(raw_txt) if raw_txt.isupper() else raw_txt
                s_title.text = clean_to_hex_entities(formatted_title)
                prev_block_type = "heading"
                continue

            active_parent = current_section if current_section is not None else current_chapter

            if b_type == "figure":
                fig_count += 1
                fig_elem = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}figure", attrib={
                    "label": str(fig_count),
                    f"{{{XML_NS}}}id": next_id()
                })
                f_info = etree.SubElement(fig_elem, f"{{{DOCBOOK_NS}}}info", attrib={f"{{{XML_NS}}}id": next_id()})
                f_title = etree.SubElement(f_info, f"{{{DOCBOOK_NS}}}title", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    f_title.append(page_pi)
                    page_pi_added = True
                f_title.text = clean_to_hex_entities(raw_txt)
                
                media_obj = etree.SubElement(fig_elem, f"{{{DOCBOOK_NS}}}mediaobject", attrib={f"{{{XML_NS}}}id": next_id()})
                etree.SubElement(media_obj, f"{{{DOCBOOK_NS}}}alt", attrib={f"{{{XML_NS}}}id": next_id()}).text = "Sample"
                img_obj = etree.SubElement(media_obj, f"{{{DOCBOOK_NS}}}imageobject", attrib={f"{{{XML_NS}}}id": next_id()})
                etree.SubElement(img_obj, f"{{{DOCBOOK_NS}}}imagedata", attrib={
                    "format": "image/jpeg",
                    "fileref": f"images/fig{fig_count}.jpg"
                })
                prev_block_type = "figure"
            elif b_type == "note":
                note_elem = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}footnote", attrib={
                    "role": "end-ch-note",
                    "label": "1",
                    f"{{{XML_NS}}}id": next_id()
                })
                p = etree.SubElement(note_elem, f"{{{DOCBOOK_NS}}}para", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    p.append(page_pi)
                    page_pi_added = True
                append_styled_spans_to_node(p, block["spans"])
                prev_block_type = "note"
            elif b_type == "blockquote":
                bq = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}blockquote", attrib={f"{{{XML_NS}}}id": next_id()})
                p = etree.SubElement(bq, f"{{{DOCBOOK_NS}}}para", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    p.append(page_pi)
                    page_pi_added = True
                append_styled_spans_to_node(p, block["spans"])
                prev_block_type = "blockquote"
            elif b_type == "sidebar":
                sb = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}sidebar", attrib={f"{{{XML_NS}}}id": next_id()})
                p = etree.SubElement(sb, f"{{{DOCBOOK_NS}}}para", attrib={f"{{{XML_NS}}}id": next_id()})
                if not page_pi_added:
                    p.append(page_pi)
                    page_pi_added = True
                append_styled_spans_to_node(p, block["spans"])
                prev_block_type = "sidebar"
            else:
                is_full_out = (prev_block_type in ("chap_title", "heading", "blockquote", "sidebar", "figure")) or (not block.get("is_indented", True))
                p_attrib = {f"{{{XML_NS}}}id": next_id()}
                if is_full_out:
                    p_attrib["role"] = "fullOut"
                p = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}para", attrib=p_attrib)
                if not page_pi_added:
                    p.append(page_pi)
                    page_pi_added = True
                append_styled_spans_to_node(p, block["spans"])
                prev_block_type = "para"

    if status_callback:
        status_callback("Normalizing hexadecimal entities and XML formatting...")

    pi_rng = etree.ProcessingInstruction("oxygen", 'RNGSchema="bloomsbury-mods.rnc"')
    pi_sch = etree.ProcessingInstruction("oxygen", 'SCHSchema="docbook-mods.sch" type="compact"')

    root.addprevious(pi_sch)
    root.addprevious(pi_rng)

    raw_xml = etree.tostring(
        root.getroottree(),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8"
    ).decode("utf-8")
    
    clean_xml = post_process_clean_xml(raw_xml)

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(clean_xml)

    if status_callback:
        status_callback("Ready")

class UniversalConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DocBook 5.0 Converter Suite")
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
            text="DocBook 5.0 XML Conversion Engine",
            font=("Arial", 13, "bold"),
            fg="#0F172A"
        ).pack(pady=(12, 2))
        
        tk.Label(
            self,
            text="Precision Pagination, Hexadecimal Entities & Bloomsbury Schema Support",
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
        self.doi_in.insert(0, "10.5040/9798216438984")
        self.doi_in.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(f, text="Book Title:").grid(row=2, column=0, sticky="w")
        self.title_in = tk.Entry(f, width=45)
        self.title_in.insert(0, "Overcoming Student Apathy")
        self.title_in.grid(row=2, column=1, padx=5, pady=5)

        self.prog_bar = ttk.Progressbar(self, mode="indeterminate", length=540)
        self.status_label = tk.Label(self, text="Ready", font=("Arial", 9), fg="#475569")
        
        self.btn = tk.Button(
            self,
            text="Generate DocBook XML",
            bg="#0284C7",
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
            args=(pdf_path, out_fn, self.doi_in.get().strip(), self.title_in.get().strip()),
            daemon=True
        )
        worker.start()

    def run_conversion_worker(self, pdf_path, out_fn, doi, title):
        try:
            parse_full_pdf(pdf_path, out_fn, doi, title, status_callback=self.set_status)
            self.after(0, lambda: self.on_conversion_success(out_fn))
        except Exception as e:
            self.after(0, lambda: self.on_conversion_error(str(e)))

    def on_conversion_success(self, out_fn):
        self.prog_bar.stop()
        self.status_label.config(text="Ready")
        self.btn.config(state="normal", text="Generate DocBook XML")
        messagebox.showinfo("Success", f"DocBook XML generated successfully!\n\nSaved to:\n{out_fn}")

    def on_conversion_error(self, err_msg):
        self.prog_bar.stop()
        self.status_label.config(text="Error occurred during conversion")
        self.btn.config(state="normal", text="Generate DocBook XML")
        messagebox.showerror("Conversion Error", f"An error occurred while generating XML:\n\n{err_msg}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = UniversalConverterApp()
    app.mainloop()
