"""Local-only document parsing helpers for Teach and artifact ingestion."""

from __future__ import annotations

from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile


_PDF_TEXT_RE = re.compile(rb"\(([^()]*)\)\s*Tj")
_PDF_ARRAY_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.DOTALL)
_PDF_ARRAY_TEXT_RE = re.compile(rb"\(([^()]*)\)")


def _decode_pdf_text(raw: bytes) -> str:
    text = raw.decode("latin-1", errors="ignore")
    return (
        text.replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\t", " ")
        .replace("\\(", "(")
        .replace("\\)", ")")
        .replace("\\\\", "\\")
    )


def extract_pdf_text(data: bytes) -> str:
    """Extract visible text from simple text-based PDFs without OCR."""
    parts: list[str] = []
    for match in _PDF_TEXT_RE.findall(data):
        decoded = _decode_pdf_text(match).strip()
        if decoded:
            parts.append(decoded)
    for block in _PDF_ARRAY_RE.findall(data):
        items = [_decode_pdf_text(item).strip() for item in _PDF_ARRAY_TEXT_RE.findall(block)]
        chunk = " ".join(item for item in items if item)
        if chunk:
            parts.append(chunk)
    return "\n".join(parts).strip()


def extract_docx_text(data: bytes) -> str:
    """Extract paragraph text from a DOCX document using standard library XML parsing."""
    with ZipFile(BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml")

    root = ET.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [
            node.text.strip()
            for node in paragraph.findall(".//w:t", namespace)
            if node.text and node.text.strip()
        ]
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs).strip()
