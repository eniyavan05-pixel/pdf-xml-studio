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

def clean_text_content(text):
    if not text:
        return ""
    # தேவையற்ற இடைவெளிகளைச் சீரமைத்தல்
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_full_pdf(pdf_path, output_xml_path, doi="10.5040/9798216353157", journal_title="Political Economy of China–Taiwan Relations"):
    doc = pymupdf.open(pdf_path)

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

    # Book Info / Metadata section
    info = etree.SubElement(root, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
    etree.SubElement(info, "title", attrib={"sortas": journal_title, f"{{{XML_NS}}}id": "b-9798216353157-0000000"}).text = journal_title
    etree.SubElement(info, "subtitle", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"}).text = "Origins and Development"

    chapter_count = 0
    current_chapter = None

    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)

    for page_idx, page in enumerate(doc):
        page_num = roman_or_arabic(page_idx + 1) # பக்க எண்கள் (i, ii, 1, 2...)
        page_text = page.get_text("text")
        
        lines = [line.strip() for line in page_text.split('\n') if line.strip()]
        if not lines:
            continue

        for line in lines:
            chap_match = chapter_regex.match(line)
            if chap_match:
                chapter_count += 1
                c_num = chap_match.group(1) or chap_match.group(3) or str(chapter_count)
                c_title = chap_match.group(2).strip() if chap_match.group(2) else f"Chapter {c_num}"

                current_chapter = etree.SubElement(root, "chapter", attrib={f"{{{XML_NS}}}id": f"b-9798216447917-chapter{chapter_count}"})
                ch_info = etree.SubElement(current_chapter, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                
                title_elem = etree.SubElement(ch_info, "title", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                # சாதாரணமாக எஸ்கேப் ஆகாமல் இருக்க நேரடியாகச் சேர்த்தல்
                title_elem.text = c_title
                title_elem.set("page-value", page_num)
                continue

            if current_chapter is None:
                chapter_count += 1
                current_chapter = etree.SubElement(root, "chapter", attrib={f"{{{XML_NS}}}id": f"b-9798216353157-intro"})
                ch_info = etree.SubElement(current_chapter, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                title_elem = etree.SubElement(ch_info, "title", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                title_elem.text = "Introduction"
                title_elem.set("page-value", page_num)

            # பாராக்களுக்குரிய டேக்கிங்
            cleaned_line = clean_text_content(line)
            if cleaned_line:
                p_node = etree.SubElement(current_chapter, "para", attrib={"role": "fullOut", f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                p_node.text = cleaned_line
                p_node.set("page-value", page_num)

    doc.close()

    # முறையான XML டாக்குமெண்ட் வடிவத்தை உருவாக்குதல்
    doctype = '<!DOCTYPE book PUBLIC "-//OASIS//DTD DocBook XML V5.0//EN" "http://www.oasis-open.org/docbook/xml/5.0/docbook.dtd">'
    raw_xml = etree.tostring(
        root,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        doctype=doctype
    ).decode("utf-8")

    # &lt; போன்ற தவறான எஸ்கேப்பிங்கை நிவர்த்தி செய்ய கூடுதல் சுத்தம் செய்தல்
    raw_xml = raw_xml.replace("&lt;?page", "<?page").replace("?&gt;", "?>")

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(raw_xml)

def roman_or_arabic(idx):
    if idx <= 4:
        return ["i", "ii", "iii", "iv"][idx - 1]
    return str(idx - 4)

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
