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

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

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

def extract_pdf_pages_clean_header(pdf_path, status_callback=None):
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    page_records = []
    
    chapter_regex = re.compile(r'^(?:Chapter\s+(\d+|[IVXLCDM]+)[:.]?\s*(.*)|(?:CHAPTER\s+(\d+|[IVXLCDM]+)))', re.IGNORECASE)
    sec_regex = re.compile(r'^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+(.*)')
    subsec_regex = re.compile(r'^([A-Z])\.\s+(.*)')
    ref_item_regex = re.compile(r'^(\[?\d+\]?)\.?\s+(.*)')
    footnote_regex = re.compile(r'^(?:(\d+)\.|\*)\s+(.*)')

    front_matter_ended = False
    front_matter_counter = 1
    chapter_page_counter = 1

    for page_idx, page in enumerate(doc, 1):
        if status_callback:
            status_callback(f"Extracting layout on page {page_idx}/{total_pages}...")

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

def parse_full_pdf(pdf_path, output_xml_path, doi="10.5040/9798216353157", journal_title="Political Economy of China–Taiwan Relations", status_callback=None):
    if status_callback:
        status_callback("Analyzing PDF pages...")
    page_records = extract_pdf_pages_clean_header(pdf_path, status_callback)

    if status_callback:
        status_callback("Building Bloomsbury Book DocBook XML structure...")

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

    # Info / Metadata Section
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

            chap_match = chapter_regex.match(raw_txt)
            if chap_match:
                chapter_count += 1
                c_num = chap_match.group(1) or chap_match.group(3) or str(chapter_count)
                c_title = chap_match.group(2).strip() if chap_match.group(2) else f"Chapter {c_num}"

                current_chapter = etree.SubElement(root, "chapter", attrib={f"{{{XML_NS}}}id": f"b-9798216447917-chapter{chapter_count}"})
                ch_info = etree.SubElement(current_chapter, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                ch_title = etree.SubElement(ch_info, "title", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                ch_title.text = f"<?page value=\"{page_num}\"?>{c_title}"

                current_sec = None
                in_chapter_references = False
                continue

            if current_chapter is None:
                chapter_count += 1
                current_chapter = etree.SubElement(root, "chapter", attrib={f"{{{XML_NS}}}id": "b-9798216353157-intro"})
                ch_info = etree.SubElement(current_chapter, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                etree.SubElement(ch_info, "title", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"}).text = f"<?page value=\"{page_num}\"?>Introduction"

            if block_type == "references_header" or raw_txt.upper().startswith("REFERENCES") or raw_txt.upper().startswith("NOTES"):
                in_chapter_references = True
                continue

            if in_chapter_references:
                ref_match = ref_item_regex.match(raw_txt)
                if ref_match:
                    r_num = ref_match.group(1).strip("[]")
                    r_text = ref_match.group(2)
                    fn_elem = etree.SubElement(current_chapter, "footnote", attrib={"role": "end-ch-note", "label": r_num, f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                    p_fn = etree.SubElement(fn_elem, "para", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                    p_fn.text = f"{r_num}.\u2002 {clean_to_hex_entities(r_text)}"
                continue

            if block_type == "footnote":
                active_parent = current_sec if current_sec is not None else current_chapter
                fn_elem = etree.SubElement(active_parent, "footnote", attrib={"role": "end-ch-note", "label": block.get('label', '1'), f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                p_fn = etree.SubElement(fn_elem, "para", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                p_fn.text = f"{block.get('label', '1')}.\u2002"
                append_styled_spans_to_node(p_fn, block["spans"])
                continue

            sec_match = sec_regex.match(raw_txt)
            if sec_match:
                roman_val = sec_match.group(1).upper()
                sec_num = ROMAN_TO_NUM.get(roman_val, current_sec_num)
                current_sec_num = sec_num
                
                current_sec = etree.SubElement(current_chapter, "section", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                sec_info = etree.SubElement(current_sec, "info", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
                etree.SubElement(sec_info, "title", attrib={f"{{{XML_NS}}}id": "b-9798216353157-0000000"}).text = sec_match.group(2).strip()
                continue

            parent_target = current_sec if current_sec is not None else current_chapter

            p_node = etree.SubElement(parent_target, "para", attrib={"role": "fullOut", f"{{{XML_NS}}}id": "b-9798216353157-0000000"})
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

    if status_callback:
        status_callback("Ready")

class UniversalConverterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TTBS - Bloomsbury DocBook XML Studio")
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
            text="TTBS Bloomsbury Book XML Studio",
            font=("Arial", 13, "bold"),
            fg="#0F172A"
        ).pack(pady=(12, 2))
        
        tk.Label(
            self,
            text="PDF to DocBook 5.0 Book Structure Conversion Suite",
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
        self.doi_in.insert(0, "10.5040/9798216353157")
        self.doi_in.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(f, text="Journal/Book Title:").grid(row=2, column=0, sticky="w")
        self.j_in = tk.Entry(f, width=45)
        self.j_in.insert(0, "Political Economy of China–Taiwan Relations")
        self.j_in.grid(row=2, column=1, padx=5, pady=5)

        self.prog_bar = ttk.Progressbar(self, mode="indeterminate", length=540)
        self.status_label = tk.Label(self, text="Ready", font=("Arial", 9), fg="#475569")
        
        self.btn = tk.Button(
            self,
            text="Generate Bloomsbury Book XML",
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

        self.btn.config(state="disabled", text="Converting Book XML...")
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
        self.btn.config(state="normal", text="Generate Bloomsbury Book XML")
        messagebox.showinfo("Success", f"Bloomsbury Book XML generated successfully!\n\nSaved to:\n{out_fn}")

    def on_conversion_error(self, err_msg):
        self.prog_bar.stop()
        self.status_label.config(text="Error occurred during conversion")
        self.btn.config(state="normal", text="Generate Quality XML")
        messagebox.showerror("Conversion Error", f"An error occurred while generating XML:\n\n{err_msg}")

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = UniversalConverterApp()
    app.mainloop()
