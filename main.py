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
    "auf", "im", "in", "den", "dem", "des", "nicht", "also", "als", "an",
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
    
    # 1. PDF-ல் உள்ளவாறே சிங்கிள் கோட் / அபாஸ்ட்ராபிக்கு அருகில் உள்ள தேவையற்ற இடைவெளிகளை அகற்றுதல்
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
    return text

def update_sections_and_links(xml_root):
    """
    அனைத்து பகுதிகளையும் (Parts), அத்தியாயங்களையும் (Chapters) மற்றும் அவற்றின் 
    லிங்க்குகளையும் (Links) ஹெக்சாடெசிமல் மற்றும் முறையான DocBook அமைப்பில் புதுப்பிக்கிறது.
    """
    for part in xml_root.findall(".//{http://docbook.org/ns/docbook}part"):
        # Part இணைப்புகளைச் சரிபார்த்தல்
        if not part.get("{http://www.w3.org/XML/1998/namespace}id"):
            part.set("{http://www.w3.org/XML/1998/namespace}id", "part-generated-id")
            
    for chapter in xml_root.findall(".//{http://docbook.org/ns/docbook}chapter"):
        # Chapter இணைப்புகளைச் சரிபார்த்தல்
        if not chapter.get("{http://www.w3.org/XML/1998/namespace}id"):
            chapter.set("{http://www.w3.org/XML/1998/namespace}id", "chapter-generated-id")
            
    return xml_root
