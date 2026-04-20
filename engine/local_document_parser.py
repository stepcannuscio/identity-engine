"""Local-only document parsing helpers for Teach and artifact ingestion."""

from __future__ import annotations

from io import BytesIO
import re
import zlib
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


_PDF_TEXT_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)\s*Tj")
_PDF_HEX_TEXT_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>\s*Tj")
_PDF_ARRAY_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.DOTALL)
_PDF_ARRAY_TEXT_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)|<([0-9A-Fa-f\s]+)>")
_PDF_STREAM_RE = re.compile(
    rb"<<(?P<dict>.*?)>>\s*stream\r?\n(?P<data>.*?)\r?\nendstream",
    re.DOTALL,
)
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


def _decode_pdf_hex_text(raw: bytes) -> str:
    compact = re.sub(rb"\s+", b"", raw)
    if len(compact) % 2 == 1:
        compact += b"0"
    try:
        decoded = bytes.fromhex(compact.decode("ascii"))
    except ValueError:
        return ""
    if not decoded:
        return ""
    if decoded.startswith((b"\xfe\xff", b"\xff\xfe")):
        try:
            return decoded.decode("utf-16", errors="ignore")
        except UnicodeDecodeError:
            return ""
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return decoded.decode("latin-1", errors="ignore")


def _candidate_pdf_buffers(data: bytes) -> list[bytes]:
    candidates: list[bytes] = [data]
    for match in _PDF_STREAM_RE.finditer(data):
        stream_dict = match.group("dict")
        stream_data = match.group("data")
        if b"/FlateDecode" in stream_dict:
            try:
                decompressed = zlib.decompress(stream_data)
            except zlib.error:
                continue
            candidates.append(decompressed)
            continue
        candidates.append(stream_data)
    return candidates


def extract_pdf_text(data: bytes) -> str:
    """Extract visible text from simple text-based PDFs without OCR."""
    parts: list[str] = []
    seen: set[str] = set()

    def _append(text: str) -> None:
        normalized = text.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            parts.append(normalized)

    for buffer in _candidate_pdf_buffers(data):
        for match in _PDF_TEXT_RE.findall(buffer):
            _append(_decode_pdf_text(match))
        for match in _PDF_HEX_TEXT_RE.findall(buffer):
            _append(_decode_pdf_hex_text(match))
        for block in _PDF_ARRAY_RE.findall(buffer):
            items: list[str] = []
            for literal_item, hex_item in _PDF_ARRAY_TEXT_RE.findall(block):
                if literal_item:
                    decoded = _decode_pdf_text(literal_item).strip()
                else:
                    decoded = _decode_pdf_hex_text(hex_item).strip()
                if decoded:
                    items.append(decoded)
            if items:
                _append(" ".join(items))
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
