"""Local-only document parsing helpers for Teach and artifact ingestion."""

from __future__ import annotations

from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


_PDF_TEXT_RE = re.compile(rb"\(([^()]*)\)\s*Tj")
_PDF_ARRAY_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.DOTALL)
_PDF_ARRAY_TEXT_RE = re.compile(rb"\(([^()]*)\)")
MAX_DOCX_DOCUMENT_XML_BYTES = 2 * 1024 * 1024


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


def extract_docx_text(
    data: bytes,
    *,
    max_document_xml_bytes: int = MAX_DOCX_DOCUMENT_XML_BYTES,
) -> str:
    """Extract paragraph text from a DOCX document using standard library XML parsing."""
    try:
        with ZipFile(BytesIO(data)) as archive:
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise ValueError("word/document.xml is missing from docx upload") from exc
            if info.file_size > max_document_xml_bytes:
                raise ValueError("docx document.xml exceeds the allowed size limit")
            document_xml = archive.read(info)
    except (BadZipFile, OSError, ValueError) as exc:
        raise ValueError("unable to extract text from docx") from exc

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise ValueError("unable to extract text from docx") from exc
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
