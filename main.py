import os
import re
import sys
import uuid
from datetime import datetime
from typing import List, Dict
import concurrent.futures
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

app = FastAPI(title="DocBook 5.1 XML Studio")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# -------------------------------------------------------------
# DOCBOOK 5.1 NAMESPACE MAPPINGS
# -------------------------------------------------------------
DOCBOOK_NS = "http://docbook.org/ns/docbook"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XLINK_NS = "http://www.w3.org/1999/xlink"
NS_MAP = {
    None: DOCBOOK_NS,
    "xml": XML_NS,
    "xlink": XLINK_NS
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
ORDERED_LIST_REGEX = re.compile(r'^\s*(\d+)[\.\)]\s+(.*)', re.DOTALL)
ITEMIZED_LIST_REGEX = re.compile(r'^\s*(?:[•\*\u2022\u25E6\u2043\u2219]|&#x2022;|-|–|—)\s+(.*)', re.DOTALL)
ATTRIBUTION_REGEX = re.compile(r'^\s*(?:—|&#x2014;|–|&#x2013;|--)\s*(.*)', re.DOTALL)

RE_CHAPTER = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
RE_SEC = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
RE_SUBSEC = re.compile(r'^([A-Z])\.\s+(.*)')
RE_LIGATURES = {ord(k): v for k, v in {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}.items()}

def clean_to_hex_entities(text: str) -> str:
    if not text:
        return ""
    text = text.translate(RE_LIGATURES)
    out_chars = []
    for char in text:
        cp = ord(char)
        if cp > 127:
            out_chars.append(f"&#x{cp:04X};" if cp <= 0xFFFF else f"&#x{cp:06X};")
        else:
            out_chars.append(char)
    text = "".join(out_chars)
    valid_xml = re.compile(r'[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')
    return valid_xml.sub('', text)

def fix_hyphenated_words(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\b(inter|intra|pre|post|macro|micro)and\b', r'\1- and', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(inter|intra|pre|post|macro|micro)or\b', r'\1- or', text, flags=re.IGNORECASE)
    text = text.replace('\u00ad', '').replace('\xad', '')

    def line_break_replacer(match):
        prefix, suffix = match.group(1), match.group(3)
        p_low, s_low = prefix.lower(), suffix.lower()
        if s_low in {"and", "or", "to", "und", "e", "ou"}:
            return f"{prefix}- {suffix}"
        if s_low in PORTUGUESE_PRONOUNS_AND_SUFFIXES or s_low in VALID_COMPOUND_WORDS or p_low in NUMBER_PREFIXES:
            return f"{prefix}-{suffix}"
        return f"{prefix}{suffix}"

    return re.sub(r'([a-zA-ZÀ-ÿ]{2,})([-‐‑])\s+([a-zA-ZÀ-ÿ]{2,})', line_break_replacer, text)

def fix_missing_boundary_spaces(text: str) -> str:
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
        ent, suffix = match.group(1), match.group(2)
        if ent in {"&#x2019;", "&#x0027;"}:
            return f"{ent}{suffix}"
        if ent in PUNCTUATION_ENTITIES or suffix.lower() in STANDALONE_WORDS:
            return f"{ent} {suffix}"
        if len(suffix) <= 5 and suffix.islower():
            return f"{ent}{suffix}"
        return f"{ent} {suffix}"

    text = re.sub(r'(&#x[0-9A-Fa-f]+;)[ \t]+([a-zA-ZÀ-ÿ]{1,10})', clean_intra_word_after, text)

    def clean_intra_word_before(match):
        prefix, ent = match.group(1), match.group(2)
        if ent in PUNCTUATION_ENTITIES or prefix.lower() in STANDALONE_WORDS:
            return f"{prefix} {ent}"
        if len(prefix) <= 4 and prefix.islower():
            return f"{prefix}{ent}"
        return f"{prefix} {ent}"

    text = re.sub(r'([a-zA-ZÀ-ÿ]{1,10})[ \t]+(&#x[0-9A-Fa-f]+;)', clean_intra_word_before, text)
    return re.sub(r'[ \t]{2,}', ' ', text)

def fast_clean_text(text: str) -> str:
    if not text:
        return ""
    text = clean_to_hex_entities(text)
    text = fix_hyphenated_words(text)
    text = fix_missing_boundary_spaces(text)
    return text.strip()

def post_process_clean_xml(xml_str: str) -> str:
    if not xml_str:
        return ""
    xml_str = re.sub(r'&amp;#x([0-9A-Fa-f]+);', r'&#x\1;', xml_str)
    xml_str = re.sub(r'&#x00A0;', ' ', xml_str)
    xml_str = re.sub(r'[ \t]+</para>', '</para>', xml_str)
    xml_str = re.sub(r'[ \t]+</foot-para>', '</foot-para>', xml_str)
    xml_str = re.sub(r'[ \t]+</sect1>', '</sect1>', xml_str)
    xml_str = re.sub(r'[ \t]+</sect2>', '</sect2>', xml_str)
    xml_str = re.sub(r'[ \t]+</chapter>', '</chapter>', xml_str)
    xml_str = re.sub(r'[ \t]+</title>', '</title>', xml_str)
    xml_str = re.sub(r'[ \t]+</attribution>', '</attribution>', xml_str)
    xml_str = re.sub(r'[ \t]+</blockquote>', '</blockquote>', xml_str)
    xml_str = re.sub(r'[ \t]+</listitem>', '</listitem>', xml_str)
    xml_str = re.sub(r'[ \t]+</orderedlist>', '</orderedlist>', xml_str)
    xml_str = re.sub(r'[ \t]+</itemizedlist>', '</itemizedlist>', xml_str)
    xml_str = re.sub(r'<para>[ \t]+', '<para>', xml_str)
    xml_str = re.sub(r'<foot-para>[ \t]+', '<foot-para>', xml_str)
    xml_str = re.sub(r'<title>[ \t]+', '<title>', xml_str)
    xml_str = re.sub(r'&#x201C;\s+', '&#x201C;', xml_str)
    xml_str = re.sub(r'\s+&#x201D;', '&#x201D;', xml_str)
    xml_str = re.sub(r'(&#x2019;|\')\s+(s|t|d|m|re|ve|ll|he|em)\b', r'\1\2', xml_str, flags=re.IGNORECASE)
    xml_str = re.sub(r'\b(inter|intra|pre|post|macro|micro)and\b', r'\1- and', xml_str, flags=re.IGNORECASE)
    xml_str = re.sub(r'\b(inter|intra|pre|post|macro|micro)or\b', r'\1- or', xml_str, flags=re.IGNORECASE)
    xml_str = re.sub(r'\s*&#x2013;\s*', '&#x2013;', xml_str)
    xml_str = re.sub(r'\s*&#x2014;\s*', '&#x2014;', xml_str)
    xml_str = re.sub(r'<para>\s*</para>', '', xml_str)
    xml_str = re.sub(r'<foot-para>\s*</foot-para>', '', xml_str)
    xml_str = re.sub(r'<blockquote>\s*</blockquote>', '', xml_str)
    xml_str = re.sub(r'<orderedlist>\s*</orderedlist>', '', xml_str)
    xml_str = re.sub(r'<itemizedlist>\s*</itemizedlist>', '', xml_str)
    return xml_str

def process_single_page(page_data: tuple) -> dict:
    pdf_path, page_idx = page_data
    doc = pymupdf.open(pdf_path)
    page = doc[page_idx]
    page_rect = page.rect
    page_width = page_rect.width
    page_height = page_rect.height

    blocks = page.get_text("blocks")
    doc.close()

    parsed_blocks = []
    valid_blocks = [b for b in blocks if 55 < b[1] < page_height - 55 and b[4].strip()]
    col_lefts = [b[0] for b in valid_blocks]
    col_rights = [b[2] for b in valid_blocks]
    base_left = min(col_lefts) if col_lefts else 50.0
    base_right = max(col_rights) if col_rights else page_width - 50.0

    for b in blocks:
        if b[6] != 0:
            continue
        x0, y0, x1, y1, raw_txt = b[0], b[1], b[2], b[3], b[4].strip()
        if not raw_txt:
            continue

        if y0 < 50 or y1 > page_height - 50:
            if re.match(r'^\d{1,5}$', raw_txt) or len(raw_txt) < 35:
                continue

        cleaned = fast_clean_text(raw_txt)
        if not cleaned:
            continue

        is_left_indented = (x0 - base_left) >= 13.0
        is_right_indented = (base_right - x1) >= 6.0
        is_quote = is_left_indented and is_right_indented and "\n" in raw_txt

        b_type = "para"
        if is_quote:
            b_type = "blockquote"
        elif ATTRIBUTION_REGEX.match(cleaned) and (x0 - base_left > 40.0):
            b_type = "attribution"
        elif ORDERED_LIST_REGEX.match(cleaned):
            if y0 > page_height - 120.0:
                b_type = "foot-para"
            else:
                b_type = "ordered_item"
        elif ITEMIZED_LIST_REGEX.match(cleaned):
            b_type = "itemized_item"

        parsed_blocks.append({"type": b_type, "text": cleaned, "raw": cleaned, "y0": y0})

    return {"page_num": str(page_idx + 1), "blocks": parsed_blocks}

def parse_full_pdf(pdf_path: str, output_xml_path: str, doi="10.5040/9798216500421", book_title="Monograph Document"):
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    tasks = [(pdf_path, idx) for idx in range(total_pages)]
    max_workers = min(os.cpu_count() or 4, 8)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        page_records = list(executor.map(process_single_page, tasks))

    root = etree.Element(
        f"{{{DOCBOOK_NS}}}book",
        attrib={
            "version": "5.1",
            f"{{{XML_NS}}}lang": "en"
        },
        nsmap=NS_MAP
    )

    info_elem = etree.SubElement(root, f"{{{DOCBOOK_NS}}}info")
    etree.SubElement(info_elem, f"{{{DOCBOOK_NS}}}title").text = book_title
    etree.SubElement(info_elem, "object-id", attrib={"pub-id-type": "doi"}).text = doi

    current_chapter = None
    current_sect1 = None
    current_sect2 = None
    current_ordered = None
    current_itemized = None
    current_quote = None
    chapter_count = 0
    sec_num = 1
    current_subsec_char = "a"

    for precord in page_records:
        page_num = precord["page_num"]

        for block in precord["blocks"]:
            txt = block["text"]
            b_type = block["type"]

            chap_match = RE_CHAPTER.match(txt)
            if chap_match:
                chapter_count += 1
                c_num = chap_match.group(1) or chap_match.group(3) or str(chapter_count)
                c_title = chap_match.group(2).strip() if chap_match.group(2) else f"Chapter {c_num}"

                current_chapter = etree.SubElement(root, f"{{{DOCBOOK_NS}}}chapter")
                current_chapter.set(f"{{{XML_NS}}}id", f"chap-{chapter_count}")
                current_chapter.append(etree.ProcessingInstruction("pb", f'n="{page_num}"'))
                
                c_info = etree.SubElement(current_chapter, f"{{{DOCBOOK_NS}}}info")
                etree.SubElement(c_info, f"{{{DOCBOOK_NS}}}title").text = c_title
                
                current_sect1 = current_sect2 = current_ordered = current_itemized = current_quote = None
                continue

            if current_chapter is None:
                chapter_count += 1
                current_chapter = etree.SubElement(root, f"{{{DOCBOOK_NS}}}chapter")
                current_chapter.set(f"{{{XML_NS}}}id", f"chap-{chapter_count}")
                current_chapter.append(etree.ProcessingInstruction("pb", f'n="{page_num}"'))
                c_info = etree.SubElement(current_chapter, f"{{{DOCBOOK_NS}}}info")
                etree.SubElement(c_info, f"{{{DOCBOOK_NS}}}title").text = "Introduction"

            sec_match = RE_SEC.match(txt)
            if sec_match:
                roman_val = sec_match.group(1).upper()
                sec_num = ROMAN_TO_NUM.get(roman_val, sec_num + 1)
                current_sect1 = etree.SubElement(current_chapter, f"{{{DOCBOOK_NS}}}sect1")
                current_sect1.set(f"{{{XML_NS}}}id", f"sec-{sec_num}")
                current_sect1.append(etree.ProcessingInstruction("pb", f'n="{page_num}"'))
                etree.SubElement(current_sect1, f"{{{DOCBOOK_NS}}}title").text = f"{sec_match.group(1)}. {sec_match.group(2).strip()}"
                
                current_sect2 = current_ordered = current_itemized = current_quote = None
                continue

            parent_target = current_sect1 if current_sect1 is not None else current_chapter

            subsec_match = RE_SUBSEC.match(txt)
            if subsec_match and len(txt) < 60 and not txt.endswith(';') and not txt.endswith(','):
                current_subsec_char = subsec_match.group(1).lower()
                current_sect2 = etree.SubElement(parent_target, f"{{{DOCBOOK_NS}}}sect2")
                current_sect2.set(f"{{{XML_NS}}}id", f"sec-{sec_num}-{current_subsec_char}")
                current_sect2.append(etree.ProcessingInstruction("pb", f'n="{page_num}"'))
                etree.SubElement(current_sect2, f"{{{DOCBOOK_NS}}}title").text = f"{subsec_match.group(1)}. {subsec_match.group(2).strip()}"

                current_ordered = current_itemized = current_quote = None
                continue

            active_parent = current_sect2 if current_sect2 is not None else (current_sect1 if current_sect1 is not None else current_chapter)

            if b_type == "foot-para":
                current_ordered = current_itemized = current_quote = None
                fp = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}foot-para")
                fp.text = txt
                continue

            if b_type == "ordered_item":
                current_itemized = current_quote = None
                if current_ordered is None or current_ordered.getparent() != active_parent:
                    current_ordered = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}orderedlist")
                li = etree.SubElement(current_ordered, f"{{{DOCBOOK_NS}}}listitem")
                p = etree.SubElement(li, f"{{{DOCBOOK_NS}}}para")
                p.text = re.sub(r'^\s*\d+[\.\)]\s*', '', txt)
                continue

            if b_type == "itemized_item":
                current_ordered = current_quote = None
                if current_itemized is None or current_itemized.getparent() != active_parent:
                    current_itemized = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}itemizedlist")
                li = etree.SubElement(current_itemized, f"{{{DOCBOOK_NS}}}listitem")
                p = etree.SubElement(li, f"{{{DOCBOOK_NS}}}para")
                p.text = re.sub(r'^\s*(?:[•\*\u2022\u25E6\u2043\u2219]|&#x2022;|-|–|—)\s*', '', txt)
                continue

            if b_type == "attribution":
                current_ordered = current_itemized = None
                if current_quote is None or current_quote.getparent() != active_parent:
                    current_quote = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}blockquote")
                att = etree.SubElement(current_quote, f"{{{DOCBOOK_NS}}}attribution", attrib={"role": "right"})
                att.text = f"&#x2014;{re.sub(r'^\s*[-–—]+\s*', '', txt)}"
                current_quote = None
                continue

            if b_type == "blockquote":
                current_ordered = current_itemized = None
                if current_quote is None or current_quote.getparent() != active_parent:
                    current_quote = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}blockquote")
                p = etree.SubElement(current_quote, f"{{{DOCBOOK_NS}}}para")
                p.text = txt
                continue

            current_ordered = current_itemized = current_quote = None
            p = etree.SubElement(active_parent, f"{{{DOCBOOK_NS}}}para")
            p.text = txt

    raw_xml = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8"
    ).decode("utf-8")

    clean_xml = post_process_clean_xml(raw_xml)

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(clean_xml)

# -------------------------------------------------------------
# FASTAPI ASYNC ENDPOINTS
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

    new_record = {
        "id": str(len(conversion_history) + 1),
        "name": file.filename,
        "date": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "size": f"{len(content) / (1024 * 1024):.2f} MB",
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
        return JSONResponse({"status": "error", "message": "Source file not found."}, status_code=404)

    target["status"] = "Processing"
    pdf_path = target["file_path"]
    base_name = os.path.splitext(target["name"])[0]
    out_xml_name = f"{base_name}.xml"
    out_xml_path = os.path.join(OUTPUTS_DIR, f"{file_id}_{out_xml_name}")

    try:
        parse_full_pdf(pdf_path, out_xml_path, book_title=base_name)
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
        return JSONResponse({"status": "error", "message": "XML file not found."}, status_code=404)

    return FileResponse(target["xml_path"], filename=target.get("xml_filename", "output.xml"), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
