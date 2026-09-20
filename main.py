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
MML_NS = "http://www.w3.org/1998/Math/MathML"
NS_MAP = {
    "ali": "http://www.niso.org/schemas/ali/1.0/",
    "xlink": XLINK_NS,
    "mml": MML_NS,
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

def int_to_roman(num):
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syb = ["m", "cm", "d", "cd", "c", "xc", "l", "xl", "x", "ix", "v", "iv", "i"]
    roman_num = ""
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            roman_num += syb[i]
            num -= val[i]
        i += 1
    return roman_num

def clean_to_hex_entities(text):
    if not text:
        return ""
    
    # சிங்கிள் கோட் / அபாஸ்ட்ராபி இடைவெளி திருத்தம்
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

def detect_and_wrap_math(text, parent_elem):
    math_pattern = re.compile(r'([A-Za-z]\s*[\+\-\*\/=<>]\s*[A-Za-z0-9]+|\b(?:sin|cos|tan|log|lim|sum|int)\b)')
    matches = list(math_pattern.finditer(text))
    
    if not matches:
        if len(parent_elem) > 0 and parent_elem[-1].tail:
            parent_elem[-1].tail += text
        elif len(parent_elem) == 0:
            parent_elem.text = (parent_elem.text or "") + text
        else:
            parent_elem[-1].tail = (parent_elem[-1].tail or "") + text
        return

    last_idx = 0
    for match in matches:
        start, end = match.span()
        if start > last_idx:
            normal_text = text[last_idx:start]
            if len(parent_elem) > 0 and parent_elem[-1].tail:
                parent_elem[-1].tail += normal_text
            elif len(parent_elem) == 0:
                parent_elem.text = (parent_elem.text or "") + normal_text
            else:
                parent_elem[-1].tail = (parent_elem[-1].tail or "") + normal_text

        math_str = text[start:end]
        mml_math = etree.SubElement(parent_elem, f"{{{MML_NS}}}math")
        mml_mi = etree.SubElement(mml_math, f"{{{MML_NS}}}mi")
        mml_mi.text = math_str
        last_idx = end

    if last_idx < len(text):
        remainder = text[last_idx:]
        mml_math.tail = (mml_math.tail or "") + remainder

def append_styled_spans_to_node(target_p, span_list):
    for span in span_list:
        raw_text = clean_to_hex_entities(span.get("text", ""))
        if not raw_text:
            continue

        url_pattern = re.compile(r'(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)')
        parts = url_pattern.split(raw_text)

        if len(parts) > 1:
            for part in parts:
                if not part:
                    continue
                if url_pattern.match(part):
                    full_url = part if part.startswith("http") else f"http://{part}"
                    uri_elem = etree.SubElement(target_p, "uri", attrib={f"{{{XLINK_NS}}}href": full_url})
                    uri_elem.text = part
                else:
                    detect_and_wrap_math(part, target_p)
        else:
            detect_and_wrap_math(raw_text, target_p)

def extract_pdf_pages_clean_header(pdf_path):
    doc = pymupdf.open(pdf_path)
    page_records = []
    
    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    subsubsec_regex = re.compile(r'^(\d+)\)\s*(.*)')
    ref_item_regex = re.compile(r'^\[(\d+)\]\s+(.*)')
    footnote_regex = re.compile(r'^(?:(\d+)\.|\*)\s+(.*)')

    front_matter_ended = False
    front_matter_counter = 1
    chapter_page_counter = 1

    for page_idx, page in enumerate(doc):
        page_height = page.rect.height
        page_blocks = page.get_text("dict").get("blocks", [])

        page_full_text = page.get_text()
        if chapter_regex.search(page_full_text) or "1." in page_full_text and page_idx > 2:
            front_matter_ended = True

        if not front_matter_ended:
            detected_page_folio = int_to_roman(front_matter_counter)
            front_matter_counter += 1
        else:
            detected_page_folio = str(chapter_page_counter)
            chapter_page_counter += 1

        blocks_list = []
        for block in page_blocks:
            if block.get("type") != 0:
                continue
            
            lines = block.get("lines", [])
            if not lines:
                continue

            current_spans = []
            for line_idx, line in enumerate(lines):
                y0 = line["bbox"][1]
                y1 = line["bbox"][3]
                line_spans = line.get("spans", [])
                if not line_spans:
                    continue

                full_line_text = clean_to_hex_entities("".join([s["text"] for s in line_spans])).strip()
                if not full_line_text:
                    continue

                if (chapter_regex.match(full_line_text) or sec_regex.match(full_line_text) or 
                    subsec_regex.match(full_line_text) or subsubsec_regex.match(full_line_text) or 
                    ref_item_regex.match(full_line_text) or full_line_text.upper().startswith("REFERENCES")):
                    
                    if current_spans:
                        blocks_list.append({"type": "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                        current_spans = []
                    
                    # References ஐத் தனிப் பிரிவாக (Heading / Ref-list) அடையாளம் காணுதல்
                    if full_line_text.upper().startswith("REFERENCES"):
                        blocks_list.append({"type": "references_header", "spans": line_spans, "raw": full_line_text})
                    else:
                        blocks_list.append({"type": "heading", "spans": line_spans, "raw": full_line_text})
                    continue

                fn_match = footnote_regex.match(full_line_text)
                if (y1 > page_height - 65) and fn_match:
                    if current_spans:
                        blocks_list.append({"type": "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                        current_spans = []
                    blocks_list.append({"type": "footnote", "spans": line_spans, "raw": full_line_text, "label": fn_match.group(1) or "*"})
                    continue

                for span in line_spans:
                    current_spans.append(span)

            if current_spans:
                blocks_list.append({"type": "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})

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

    front = etree.SubElement(root, "front")
    b_meta = etree.SubElement(front, "book-meta")
    etree.SubElement(etree.SubElement(b_meta, "book-title-group"), "book-title").text = journal_title
    etree.SubElement(b_meta, "object-id", attrib={"pub-id-type": "doi"}).text = doi

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

            if block_type == "references_header" or raw_txt.upper().startswith("REFERENCES"):
                in_references = True
                continue

            if in_references:
                ref_match = ref_item_regex.match(raw_txt)
                if ref_match:
                    ref_items.append((ref_match.group(1), ref_match.group(2)))
                elif ref_items:
                    last_n, last_t = ref_items[-1]
                    ref_items[-1] = (last_n, last_t + " " + raw_txt)
                else:
                    # ஒருவேளை பிராக்கெட் இல்லாத ரெஃபரன்ஸ் லைனாக இருந்தால் அப்படியே சேர்த்துக்கொள்ளும்
                    ref_items.append((str(len(ref_items) + 1), raw_txt))
                continue

            # Chapter Title Tagging
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
                continue

            if current_chapter is None:
                chapter_count += 1
                current_chapter = etree.SubElement(body, "chapter")
                current_chapter.set("id", f"chap{chapter_count}")
                etree.SubElement(current_chapter, "label").text = f"Chapter {chapter_count}"
                etree.SubElement(current_chapter, "title").text = "Introduction"

            # Footnote Tagging
            if block_type == "footnote":
                active_parent = current_subsubsec or current_subsec or current_sec or current_chapter
                fn_elem = etree.SubElement(active_parent, "fn", attrib={"id": f"fn-{page_num}-{block.get('label', '1')}"})
                etree.SubElement(fn_elem, "label").text = block.get('label', '1')
                p_fn = etree.SubElement(fn_elem, "p")
                append_styled_spans_to_node(p_fn, block["spans"])
                continue

            # Section Title Tagging
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

            # Subsection Tagging
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

            active_parent = current_subsubsec or current_subsec or current_sec or current_chapter

            if not page_marker_inserted:
                page_marker = etree.SubElement(active_parent, "named-content")
                page_marker.set("content-type", "page-id")
                page_marker.set("id", f"page-{page_num}")
                page_marker_inserted = True

            p_node = etree.SubElement(active_parent, "p")
            append_styled_spans_to_node(p_node, block["spans"])

    # 3. Back Matter (References Section Generation)
    back = etree.SubElement(root, "back")
    ref_list = etree.SubElement(back, "ref-list")
    etree.SubElement(ref_list, "title").text = "References"

    for r_num, r_text in ref_items:
        clean_ref = clean_to_hex_entities(r_text).strip()
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

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(raw_xml)

# FastAPI Endpoints
conversion_history: List[Dict] = []

@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

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
    if not target:
        return JSONResponse({"status": "error", "message": "File not found."}, status_code=404)

    out_xml_path = os.path.join(OUTPUTS_DIR, f"{file_id}_{os.path.splitext(target['name'])[0]}.xml")
    try:
        parse_full_pdf(target["file_path"], out_xml_path)
        target["status"] = "Done"
        target["xml_path"] = out_xml_path
        return JSONResponse({"status": "success", "download_url": f"/api/download/{target['id']}"})
    except Exception as err:
        return JSONResponse({"status": "error", "message": str(err)}, status_code=500)

@app.get("/api/download/{item_id}")
async def download_xml_file(item_id: str):
    target = next((item for item in conversion_history if item.get("id") == item_id), None)
    return FileResponse(target["xml_path"], filename=os.path.basename(target["xml_path"]), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
