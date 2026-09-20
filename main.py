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
import urllib.parse

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
    None: "http://docbook.org/ns/docbook",
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
    
    # சிங்கிள் கோட் மற்றும் அபாஸ்ட்ராபி இடைவெளி திருத்தம்
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
                    uri_elem = etree.SubElement(target_p, "link", attrib={f"{{{XLINK_NS}}}href": full_url})
                    uri_sub = etree.SubElement(uri_elem, "uri")
                    uri_sub.text = part
                else:
                    target_p.text = (target_p.text or "") + part
        else:
            if len(target_p) > 0 and target_p[-1].tail:
                target_p[-1].tail += raw_text
            elif len(target_p) == 0:
                target_p.text = (target_p.text or "") + raw_text
            else:
                target_p[-1].tail = (target_p[-1].tail or "") + raw_text

def extract_pdf_pages_clean_header(pdf_path):
    doc = pymupdf.open(pdf_path)
    page_records = []
    
    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    ref_item_regex = re.compile(r'^(\[?\d+\]?)\.?\s+(.*)')
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
                    subsec_regex.match(full_line_text) or ref_item_regex.match(full_line_text) or 
                    full_line_text.upper().startswith("REFERENCES") or full_line_text.upper().startswith("NOTES")):
                    
                    if current_spans:
                        blocks_list.append({"type": "para", "spans": current_spans, "raw": "".join([s["text"] for s in current_spans]).strip()})
                        current_spans = []
                    
                    if full_line_text.upper().startswith("REFERENCES") or full_line_text.upper().startswith("NOTES"):
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

def parse_full_pdf(pdf_path, output_xml_path, doi="10.5040/9798216353157", journal_title="Political Economy of China–Taiwan Relations"):
    page_records = extract_pdf_pages_clean_header(pdf_path)

    root = etree.Element(
        "book",
        attrib={
            "version": "5.0",
            f"{{{XML_NS}}}lang": "en",
            "role": "fullText",
            f"{{{XML_NS}}}id": "b-9798216353157"
        },
        nsmap=NS_MAP
    )

    # Book Info / Metadata section matching sample XML
    info = etree.SubElement(root, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
    etree.SubElement(info, "title", attrib={"sortas": journal_title, f"{{{XML_NS}}}id": "b-9798216353157-0000000"}).text = journal_title
    etree.SubElement(info, "subtitle", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"}).text = "Origins and Development"

    current_chapter = None
    current_sec = None
    chapter_count = 0
    current_sec_num = 1
    in_chapter_references = False

    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    ref_item_regex = re.compile(r'^(\[?\d+\]?)\.?\s+(.*)')

    for precord in page_records:
        page_num = precord["page_num"]
        blocks_to_process = precord["blocks"]

        for block in blocks_to_process:
            raw_txt = block["raw"]
            block_type = block.get("type", "para")

            # Chapter Title Tagging
            chap_match = chapter_regex.match(raw_txt)
            if chap_match:
                chapter_count += 1
                c_num = chap_match.group(1) or chap_match.group(3) or str(chapter_count)
                c_title = chap_match.group(2).strip() if chap_match.group(2) else f"Chapter {c_num}"

                current_chapter = etree.SubElement(root, "chapter", attrib={f"{{{XML_NS}}}id": f"b-9798216447917-chapter{chapter_count}"})
                ch_info = etree.SubElement(current_chapter, "info", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                ch_title = etree.SubElement(ch_info, "title", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                ch_title.text = f"<?page value=\"{page_num}\"?>{c_title}"

                current_sec = None
                in_chapter_references = False
                continue

            if current_chapter is None:
                chapter_count += 1
                current_chapter = etree.SubElement(root, "chapter", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-intro"})
                ch_info = etree.SubElement(current_chapter, "info", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                etree.SubElement(ch_info, "title", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"}).text = f"<?page value=\"{page_num}\"?>Introduction"

            # End of Chapter References / Notes
            if block_type == "references_header" or raw_txt.upper().startswith("REFERENCES") or raw_txt.upper().startswith("NOTES"):
                in_chapter_references = True
                continue

            if in_chapter_references:
                ref_match = ref_item_regex.match(raw_txt)
                if ref_match:
                    r_num = ref_match.group(1).strip("[]")
                    r_text = ref_match.group(2)
                    fn_elem = etree.SubElement(current_chapter, "footnote", attrib={"role": "end-ch-note", "label": r_num, f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                    p_fn = etree.SubElement(fn_elem, "para", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                    p_fn.text = f"{r_num}.\u2002 {clean_to_hex_entities(r_text)}"
                continue

            # Footnote Tagging
            if block_type == "footnote":
                active_parent = current_sec if current_sec is not None else current_chapter
                fn_elem = etree.SubElement(active_parent, "footnote", attrib={"role": "end-ch-note", "label": block.get('label', '1'), f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                p_fn = etree.SubElement(fn_elem, "para", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                p_fn.text = f"{block.get('label', '1')}.\u2002"
                append_styled_spans_to_node(p_fn, block["spans"])
                continue

            # Section Title Tagging
            sec_match = sec_regex.match(raw_txt)
            if sec_match:
                roman_val = sec_match.group(1).upper()
                sec_num = ROMAN_TO_NUM.get(roman_val, current_sec_num)
                current_sec_num = sec_num
                
                current_sec = etree.SubElement(current_chapter, "section", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                sec_info = etree.SubElement(current_sec, "info", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
                etree.SubElement(sec_info, "title", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-0000000"}).text = sec_match.group(2).strip()
                continue

            parent_target = current_sec if current_sec is not None else current_chapter

            # Paragraph Tagging with Page values
            p_node = etree.SubElement(parent_target, "para", attrib={"role": "fullOut", f"{{{XML_NS}}}id": f"b-9798216353157-0000000"})
            
            if page_num:
                p_node.text = f"<?page value=\"{page_num}\"?>"

            append_styled_spans_to_node(p_node, block["spans"])

    doctype = '<!DOCTYPE book PUBLIC "-//OASIS//DTD DocBook XML V5.0//EN" "http://www.oasis-open.org/docbook/xml/5.0/docbook.dtd">'
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
        return JSONResponse({
            "status": "success", 
            "download_url": f"/api/download/{target['id']}",
            "filename": os.path.basename(out_xml_path)
        })
    except Exception as err:
        return JSONResponse({"status": "error", "message": str(err)}, status_code=500)

@app.get("/api/download/{item_id}")
async def download_xml_file(item_id: str):
    target = next((item for item in conversion_history if item.get("id") == item_id), None)
    if not target or "xml_path" not in target:
        return JSONResponse({"status": "error", "message": "File not found."}, status_code=404)
        
    file_path = target["xml_path"]
    filename = os.path.basename(file_path)
    
    headers = {
        'Content-Disposition': f'attachment; filename="{urllib.parse.quote(filename)}"'
    }
    return FileResponse(file_path, headers=headers, media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
