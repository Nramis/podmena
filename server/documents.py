"""Extract editable text from supported uploads without retaining source files."""
from io import BytesIO
from pathlib import PurePath
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from excel import WorkbookError

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT = 250_000
PLAIN_FORMATS = {".txt", ".md", ".csv", ".tsv", ".log", ".json"}
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


HIDDEN_WORD_TEXT = {WORD + "del", WORD + "moveFrom", WORD + "txbxContent"}


def word_runs(element):
    if element.tag in HIDDEN_WORD_TEXT:
        return
    if element.tag == WORD + "t":
        yield element.text or ""
    elif element.tag == WORD + "tab":
        yield "\t"
    elif element.tag in {WORD + "br", WORD + "cr"}:
        yield "\n"
    else:
        for child in element:
            yield from word_runs(child)


def word_paragraphs(element):
    if element.tag in HIDDEN_WORD_TEXT:
        return
    if element.tag == WORD + "p":
        yield "".join(word_runs(element))
    else:
        for child in element:
            yield from word_paragraphs(child)


def read_docx(data):
    with ZipFile(BytesIO(data)) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 8 * 1024 * 1024:
            raise WorkbookError("Основной текст документа Word слишком велик.")
        body = fromstring(archive.read(info)).find(WORD + "body")
        if body is None:
            raise WorkbookError("Не удалось найти основной текст документа Word.")
        return "\n".join(word_paragraphs(body))


def read_pdf(data):
    reader = PdfReader(BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise WorkbookError("PDF защищён паролем.")
    if len(reader.pages) > 120:
        raise WorkbookError("Поддерживаются PDF до 120 страниц.")
    pages = []
    size = 0
    for page in reader.pages:
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 2 * 1024 * 1024:
            raise WorkbookError("Одна из страниц PDF слишком сложна для извлечения текста.")
        page_text = page.extract_text()
        size += len(page_text) + 2
        if size > MAX_TEXT:
            raise WorkbookError("В документе больше 250 000 символов.")
        pages.append(page_text)
    return "\n\n".join(pages)


def read_document(name, data):
    extension = PurePath(name).suffix.lower()
    try:
        if extension in PLAIN_FORMATS:
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            result = data.decode(encoding)
        elif extension == ".docx":
            result = read_docx(data)
        elif extension == ".pdf":
            result = read_pdf(data)
        else:
            raise WorkbookError("Выберите TXT, Markdown, CSV, TSV, LOG, JSON, DOCX или PDF.")
    except WorkbookError:
        raise
    except UnicodeDecodeError as exc:
        raise WorkbookError("Сохраните текстовый файл в UTF-8 или UTF-16 с BOM.") from exc
    except (PyPdfError, BadZipFile, DefusedXmlException, ParseError, OSError, KeyError,
            ValueError, TypeError, IndexError, RecursionError, RuntimeError) as exc:
        raise WorkbookError("Не удалось прочитать документ. Проверьте формат файла.") from exc

    result = result.replace("\r\n", "\n").replace("\r", "\n")
    if "\0" in result or not result.strip():
        raise WorkbookError("В файле нет доступного текста. Для скана используйте распознавание и вставьте текст.")
    if len(result) > MAX_TEXT:
        raise WorkbookError("В документе больше 250 000 символов.")
    return result
