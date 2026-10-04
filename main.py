# -*- coding: utf-8 -*-
import os
import re
import uuid
import tempfile
import pymupdf
from lxml import etree
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangnum

app = FastAPI(title="FlyingBees - XML Conversion Suite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_STORAGE = {}
CONVERSION_HISTORY = []

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

STANDALONE_WORDS = {
    "sich", "und", "der", "die", "das", "ein", "eine", "mit", "von", "zu",
    "auf", "im", "in", "den", "dem", "des", "nicht", "auch", "als", "an",
    "the", "and", "a", "an", "of", "in", "to", "for", "with", "on", "at"
}

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
        if s_lower in VALID_COMPOUND_WORDS or prefix.isupper() or suffix.isupper() or any(c.isdigit() for c in full_match):
            return full_match
        if len(suffix) <= 4 and suffix.islower():
            return prefix + suffix
        return full_match

    return re.sub(r'\b([a-zA-Z]{2,})[-‐‑]([a-zA-Z]{2,})\b', option_hyphen_replacer, text)

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
        ent, suffix = match.group(1), match.group(2)
        if ent in PUNCTUATION_ENTITIES or suffix.lower() in STANDALONE_WORDS:
            return f"{ent} {suffix}"
        if len(suffix) <= 5 and suffix.islower():
            return f"{ent}{suffix}"
        return f"{ent} {suffix}"

    text = re.sub(r'(&#x[0-9A-Fa-f]+;)[ \t]+([a-zA-Z]{1,10})', clean_intra_word_after, text)

    def clean_intra_word_before(match):
        prefix, ent = match.group(1), match.group(2)
        if ent in PUNCTUATION_ENTITIES or prefix.lower() in STANDALONE_WORDS:
            return f"{prefix} {ent}"
        if len(prefix) <= 4 and prefix.islower():
            return f"{prefix}{ent}"
        return f"{prefix} {ent}"

    text = re.sub(r'([a-zA-Z]{1,10})[ \t]+(&#x[0-9A-Fa-f]+;)', clean_intra_word_before, text)
    return re.sub(r'[ \t]{2,}', ' ', text)

def post_process_clean_xml(xml_str):
    if not xml_str:
        return ""
    xml_str = re.sub(r'&amp;#x([0-9A-Fa-f]+);', r'&#x\1;', xml_str)
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
    current_text, current_style = "", None
    for span in span_list:
        text = clean_to_hex_entities(span.get("text", ""))
        text = fix_hyphenated_words(text)
        text = fix_missing_boundary_spaces(text)
        if not text:
            continue
        font, flags, pos_type = span.get("font", "").lower(), span.get("flags", 0), span.get("pos_type", "regular")
        is_bold = (flags & 2**4) != 0 or "bold" in font or "black" in font
        is_italic = (flags & 2**1) != 0 or "italic" in font or "oblique" in font
        style_parts = []
        if pos_type in ("sup", "sub"): style_parts.append(pos_type)
        if is_bold: style_parts.append("bold")
        if is_italic: style_parts.append("italic")
        style = "_".join(style_parts) if style_parts else "regular"
        if text.isspace() and current_style is not None:
            current_text += text
            continue
        if current_style is None:
            current_style, current_text = style, text
        elif current_style == style:
            current_text += text
        else:
            if current_text: merged.append({"style": current_style, "text": current_text})
            current_style, current_text = style, text
    if current_text: merged.append({"style": current_style, "text": current_text})
    return merged

def append_styled_spans_to_node(target_p, span_list):
    merged_spans = merge_consecutive_styled_spans(span_list)
    for item in merged_spans:
        raw_text, style = item["text"], item["style"]
        if not raw_text: continue
        leading_ws, trailing_ws = len(raw_text) - len(raw_text.lstrip(' ')), len(raw_text) - len(raw_text.rstrip(' '))
        core_text = raw_text.strip(' ')
        if not core_text:
            if len(target_p) > 0: target_p[-1].tail = (target_p[-1].tail or "") + raw_text
            else: target_p.text = (target_p.text or "") + raw_text
            continue
        if leading_ws > 0:
            lead_str = " " * leading_ws
            if len(target_p) > 0: target_p[-1].tail = (target_p[-1].tail or "") + lead_str
            else: target_p.text = (target_p.text or "") + lead_str

        container_elem, leaf_node = None, target_p
        if "sup" in style or "sub" in style or "bold" in style or "italic" in style:
            tag_order = []
            if "sup" in style: tag_order.append("sup")
            elif "sub" in style: tag_order.append("sub")
            if "bold" in style: tag_order.append("bold")
            if "italic" in style: tag_order.append("italic")
            container_elem = etree.SubElement(target_p, tag_order[0])
            curr_elem = container_elem
            for next_tag in tag_order[1:]: curr_elem = etree.SubElement(curr_elem, next_tag)
            leaf_node = curr_elem

        pattern = re.compile(r'(\[(?:\d+)(?:,\s*\d+)*\]|(?:Eq\.\s*|Equation\s*)?\(\d+\)|Fig(?:ure)?\.\s*\d+|Table\s+[IVXLCDM\d]+|Section\s+[IVXLCDM\d]+)')
        tokens = pattern.split(core_text)
        for token in tokens:
            if not token: continue
            bibr_match, multi_bibr = re.fullmatch(r'\[(\d+)\]', token), re.fullmatch(r'\[([\d,\s]+)\]', token)
            eqn_match, fig_match = re.search(r'\((\d+)\)', token), re.search(r'Fig(?:ure)?\.\s*(\d+)', token, re.IGNORECASE)
            tbl_match, sec_match = re.search(r'Table\s+([IVXLCDM\d]+)', token, re.IGNORECASE), re.search(r'Section\s+([IVXLCDM\d]+)', token, re.IGNORECASE)

            if bibr_match:
                etree.SubElement(leaf_node, "xref", attrib={"ref-type": "bibr", "rid": f"ref{bibr_match.group(1)}"}).text = token
            elif multi_bibr:
                nums = [n.strip() for n in multi_bibr.group(1).split(",") if n.strip()]
                for idx, num in enumerate(nums):
                    xr = etree.SubElement(leaf_node, "xref", attrib={"ref-type": "bibr", "rid": f"ref{num}"})
                    xr.text = f"[{num}]"
                    if idx < len(nums) - 1: xr.tail = ", "
            elif eqn_match and ("Eq" in token or token.startswith("(")):
                etree.SubElement(leaf_node, "xref", attrib={"ref-type": "disp-formula", "rid": f"deqn{eqn_match.group(1)}"}).text = token
            elif fig_match:
                etree.SubElement(leaf_node, "xref", attrib={"ref-type": "fig", "rid": f"fig{fig_match.group(1)}"}).text = token
            elif tbl_match:
                t_num = ROMAN_TO_NUM.get(tbl_match.group(1).upper(), tbl_match.group(1).upper())
                etree.SubElement(leaf_node, "xref", attrib={"ref-type": "table", "rid": f"table{t_num}"}).text = token
            elif sec_match:
                s_num = ROMAN_TO_NUM.get(sec_match.group(1).upper(), sec_match.group(1).upper())
                etree.SubElement(leaf_node, "xref", attrib={"ref-type": "sec", "rid": f"sec{s_num}"}).text = token
            else:
                if len(leaf_node) > 0:
                    if leaf_node[-1].tail: leaf_node[-1].tail += token
                    else: leaf_node[-1].tail = token
                else: leaf_node.text = (leaf_node.text or "") + token

        if trailing_ws > 0:
            trail_str = " " * trailing_ws
            if container_elem is not None: container_elem.tail = (container_elem.tail or "") + trail_str
            else:
                if len(target_p) > 0: target_p[-1].tail = (target_p[-1].tail or "") + trail_str
                else: target_p.text = (target_p.text or "") + trail_str

def is_actual_running_header(line_text, y0, page_height):
    t = line_text.strip()
    if not t: return True
    if y0 < 50 or y0 > (page_height - 40):
        if re.search(r'^(?:\d+\s+)?Chapter\s+\d+', t, re.IGNORECASE) or re.match(r'^\d{1,5}$', t) or ("IEEE" in t and len(t) < 45):
            return True
    return False

def extract_exact_page_number(page, last_confirmed_page):
    page_dict, pw, ph = page.get_text("dict"), page.rect.width, page.rect.height
    detected_folio = None
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0: continue
        for line in block.get("lines", []):
            x0, x1, y0, y1 = line["bbox"][0], line["bbox"][2], line["bbox"][1], line["bbox"][3]
            if y0 < 50 or y1 > ph - 40:
                l_text = "".join([s.get("text", "") for s in line.get("spans", [])]).strip()
                if l_text.isdigit() and 1 <= int(l_text) <= 99999:
                    detected_folio = int(l_text)
                    break
                if re.match(r'^(\d{1,5})\b', l_text) and x0 < pw * 0.40:
                    detected_folio = int(re.match(r'^(\d{1,5})\b', l_text).group(1))
                    break
        if detected_folio is not None: break
    return detected_folio if detected_folio is not None else ((last_confirmed_page + 1) if last_confirmed_page is not None else page.number + 1)

def extract_pdf_pages_clean_header(pdf_path):
    doc = pymupdf.open(pdf_path)
    page_records = []
    sec_regex, subsec_regex, subsubsec_regex, ref_item_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)'), re.compile(r'^([A-Z])\.\s+(.*)'), re.compile(r'^(\d+)\)\s*(.*)'), re.compile(r'^\[(\d+)\]\s+(.*)')
    last_folio = None

    for idx, page in enumerate(doc, 1):
        detected_page_folio = extract_exact_page_number(page, last_folio)
        last_folio = detected_page_folio
        blocks_list, page_dict, ph, page_blocks = [], page.get_text("dict"), page.rect.height, page.get_text("dict").get("blocks", [])
        text_x0s = [b["bbox"][0] for b in page_blocks if b.get("type") == 0 and b.get("lines")]
        column_base_x0 = min(text_x0s) if text_x0s else 50.0

        for block in page_blocks:
            if block.get("type") != 0: continue
            lines = block.get("lines", [])
            if not lines: continue
            current_spans, block_x0, base_x0 = [], block["bbox"][0], lines[0]["bbox"][0]
            is_blockquote = (block_x0 - column_base_x0) > 14.0

            for line in lines:
                y0, line_spans = line["bbox"][1], line.get("spans", [])
                if not line_spans: continue
                full_line_text = clean_to_hex_entities("".join([s["text"] for s in line_spans])).strip()
                if not full_line_text or is_actual_running_header(full_line_text, y0, ph): continue
                full_line_text = fix_hyphenated_words(full_line_text)

                sizes = [s["size"] for s in line_spans if s.get("text", "").strip()]
                dominant_size = max(set(sizes), key=sizes.count) if sizes else 10.0
                baseline_y = line_spans[0]["origin"][1] if "origin" in line_spans[0] else line["bbox"][3]

                for s_i, span in enumerate(line_spans):
                    span_copy = dict(span)
                    if not span_copy.get("text", ""): continue
                    s_size = span_copy.get("size", dominant_size)
                    s_origin_y = span_copy.get("origin", (0, baseline_y))[1]
                    span_copy["pos_type"] = "sup" if s_size < dominant_size * 0.85 and s_origin_y < baseline_y - 1.2 else ("sub" if s_size < dominant_size * 0.85 and s_origin_y > baseline_y + 1.0 else "regular")
                    if s_i < len(line_spans) - 1 and (line_spans[s_i + 1]["bbox"][0] - span["bbox"][2]) > 2.0 and not span_copy["text"].endswith(" "):
                        span_copy["text"] += " "
                    current_spans.append(span_copy)

                if current_spans and not any(current_spans[-1]["text"].endswith(c) for c in ['-', '‐', '‑']):
                    current_spans.append({"text": " ", "flags": 0, "size": dominant_size, "font": "", "pos_type": "regular"})

                line_x0, is_indented = line["bbox"][0], (line["bbox"][0] - base_x0) > 4.0
                if sec_regex.match(full_line_text) or subsec_regex.match(full_line_text) or subsubsec_regex.match(full_line_text) or ref_item_regex.match(full_line_text) or "REFERENCES" in full_line_text.upper():
                    if current_spans:
                        blocks_list.append({"type": "disp-quote" if is_blockquote else "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                        current_spans = []
                    blocks_list.append({"type": "heading", "spans": line_spans, "raw": full_line_text})
                    base_x0 = line_x0
                    continue

                if is_indented and current_spans:
                    blocks_list.append({"type": "disp-quote" if is_blockquote else "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                    current_spans = []
                base_x0 = line_x0

            if current_spans:
                blocks_list.append({"type": "disp-quote" if is_blockquote else "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
        page_records.append({"page_num": str(detected_page_folio), "blocks": blocks_list})
    return page_records

def parse_reference_strict(ref_text):
    raw = fix_missing_boundary_spaces(fix_hyphenated_words(clean_to_hex_entities(ref_text).strip()))
    info = {"authors": [], "has_etal": False, "article_title": "", "source": "", "volume": "", "issue": "", "fpage": "", "lpage": "", "month": "", "year": ""}
    title_match = re.search(r'(?:&#x201C;|[\u201c"])(.*?)(?:,&#x201D;|,"|[\u201d"])', raw)
    if title_match:
        info["article_title"] = title_match.group(1).strip()
        authors_part, rest_part = raw[:title_match.start()].strip(), raw[title_match.end():].strip()
    else:
        authors_part, rest_part = raw, ""

    if "et al" in authors_part:
        info["has_etal"] = True
        authors_part = re.sub(r',?\s*et al\.?', '', authors_part)

    for name in re.split(r'\s+and\s+|,\s*|\s*&\s*', authors_part.rstrip(",")):
        name = name.strip().rstrip(".")
        if not name: continue
        tokens = name.split()
        if len(tokens) >= 2: info["authors"].append((" ".join(tokens[:-1]) + ".", tokens[-1]))
        elif len(tokens) == 1: info["authors"].append(("", tokens[0]))

    if rest_part.startswith(","): rest_part = rest_part[1:].strip()
    ym = re.search(r'\b(19\d{2}|20\d{2})\b', rest_part)
    if ym: info["year"] = ym.group(1)
    pm = re.search(r'pp\.\s*(\d+)(?:&#x2013;|[\u2013\-])+(\d+)', rest_part)
    if pm: info["fpage"], info["lpage"] = pm.group(1), pm.group(2)
    vm = re.search(r'vol\.\s*(\d+)', rest_part)
    if vm: info["volume"] = vm.group(1)
    ism = re.search(r'no\.\s*(\d+)', rest_part)
    if ism: info["issue"] = ism.group(1)
    return info

def parse_full_pdf(pdf_path, output_xml_path, doi, journal_title):
    page_records = extract_pdf_pages_clean_header(pdf_path)
    root = etree.Element("article", attrib={"article-type": "research", "content-type": "orig-research", "dtd-version": "2.0", "lifecycle": "final", "open-access": "no", "peer-reviewed": "yes", f"{{{XML_NS}}}lang": "eng"}, nsmap=NS_MAP)

    front = etree.SubElement(root, "front")
    j_meta = etree.SubElement(front, "journal-meta")
    etree.SubElement(j_meta, "journal-id", attrib={"journal-id-type": "acronym"}).text = "LWC"
    etree.SubElement(etree.SubElement(j_meta, "journal-title-group"), "journal-title").text = journal_title
    etree.SubElement(j_meta, "issn", attrib={"publication-format": "print"}).text = "2162-2337"
    etree.SubElement(j_meta, "issn", attrib={"publication-format": "online"}).text = "2162-2345"
    etree.SubElement(etree.SubElement(j_meta, "publisher"), "publisher-name").text = "IEEE"

    art_meta = etree.SubElement(front, "article-meta")
    etree.SubElement(art_meta, "object-id", at
