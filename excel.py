"""Reversible text and numeric replacement with structure-preserving XLSX processing."""
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from math import isfinite
from secrets import randbelow
from sys import float_info
from zipfile import ZipFile, BadZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import iterparse
from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.cell.rich_text import CellRichText
from openpyxl.utils.cell import range_boundaries

MAX_FILE = 20 * 1024 * 1024
MAX_CELLS = 300_000
MAX_EXPANDED = 128 * 1024 * 1024
MAX_OUTPUT_TEXT = 64 * 1024 * 1024
MAX_PAIRS = 1_000
MAX_PAIR_LENGTH = 1_024
MAX_ROWS, MAX_COLS = 1_048_576, 16_384
MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
X15 = "{http://schemas.microsoft.com/office/spreadsheetml/2010/11/main}"
CALC_FEATURES = "{http://schemas.microsoft.com/office/spreadsheetml/2018/calcfeatures}"
CHART_TRACKING_EXT = "{140A7094-0E35-4892-8432-C4D2E57EDEB5}"
CALC_FEATURES_EXT = "{B58B0392-4F1F-4190-BB64-5DF3571DCE5F}"
VML_SHAPE = "{urn:schemas-microsoft-com:vml}shape"
VML_CLIENT = "{urn:schemas-microsoft-com:office:excel}ClientData"
AUTO_TOKEN = re.compile(r"⟦PM_[A-Z0-9_]+⟧")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+)(?:[eE][+-]?[0-9]+)?")
UNSUPPORTED = re.compile(
    r"(^|/)(drawings|charts|pivottables|pivotcache|embeddings|activex|ctrlprops|"
    r"slicers|slicercaches|querytables|threadedcomments|persons|model|customxml|richdata|"
    r"connections\.xml|vbaproject\.bin|_xmlsignatures)(/|$)", re.I
)


class WorkbookError(ValueError):
    pass


@dataclass(frozen=True)
class Area:
    sheet: int
    top: int
    left: int
    bottom: int
    right: int

    def contains(self, row, col):
        return self.top <= row <= self.bottom and self.left <= col <= self.right


class Matcher:
    def __init__(self, pairs):
        pairs = validate_pairs(pairs)
        self.forward = dict(pair for pair in pairs if len(pair) == 2)
        self.backward = {token: original for original, token in self.forward.items()}
        self.pattern = self.compile(self.forward)
        self.reverse = self.compile(self.backward)
        self.numbers = {parse_number(pair[0]): parse_number(pair[1]) for pair in pairs if len(pair) == 3}
        self.reverse_numbers = {b: a for a, b in self.numbers.items()}

    @staticmethod
    def compile(mapping):
        return re.compile("|".join(map(re.escape, sorted(mapping, key=len, reverse=True))) or r"(?!)")

    def replace(self, text, restore=False, limit=32767):
        pattern, mapping = (self.reverse, self.backward) if restore else (self.pattern, self.forward)
        return bounded_replace(text, pattern, mapping, limit)

    def unknown(self, text):
        return sorted(set(AUTO_TOKEN.findall(text)) - self.backward.keys())[:20]


def bounded_replace(text, pattern, mapping, limit):
    pieces = []
    end = size = 0
    for match in pattern.finditer(text):
        replacement = mapping[match.group()]
        size += match.start() - end + len(replacement)
        if size > limit:
            raise WorkbookError("Результат замены превышает допустимую длину.")
        pieces.extend((text[end:match.start()], replacement))
        end = match.end()
    if size + len(text) - end > limit:
        raise WorkbookError("Результат замены превышает допустимую длину.")
    if not pieces:
        return text, 0
    count = len(pieces) // 2
    pieces.append(text[end:])
    return "".join(pieces), count


def parse_number(value):
    value = value.strip()
    error = "Введите число Excel с точкой или запятой и поддерживаемой точностью."
    if not NUMBER.fullmatch(value):
        raise WorkbookError(error)
    try:
        decimal = Decimal(value.replace(",", "."))
        number = float(decimal)
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise WorkbookError(error) from exc
    # XLSX serialization by openpyxl writes at most 16 significant digits.
    if (not isfinite(number) or (number and abs(number) < float_info.min)
            or Decimal(format(number, ".16g")) != decimal):
        raise WorkbookError(error)
    return number


def pair_data(pair):
    result = {"original": pair[0], "substitute": pair[1]}
    if len(pair) == 3:
        result["kind"] = pair[2]
    return result


def validate_pairs(pairs, allow_empty=False):
    pairs = [tuple(pair) for pair in pairs]
    if (not pairs and not allow_empty) or len(pairs) > MAX_PAIRS:
        raise WorkbookError(f"Укажите от 1 до {MAX_PAIRS} пар подмены.")
    originals, substitutes = set(), set()
    for pair in pairs:
        original, substitute = pair[:2]
        if any(not value.strip() or len(value) > MAX_PAIR_LENGTH or ILLEGAL_CHARACTERS_RE.search(value)
               for value in (original, substitute)):
            raise WorkbookError(f"Заполните обе части каждой пары. Максимум {MAX_PAIR_LENGTH} символов.")
        if len(pair) == 3:
            original, substitute = parse_number(original), parse_number(substitute)
        if original == substitute:
            raise WorkbookError("Исходное значение и его подстановка должны различаться.")
        if original in originals or substitute in substitutes:
            raise WorkbookError("Исходные значения и подстановки должны быть уникальными.")
        originals.add(original)
        substitutes.add(substitute)
    return pairs


def check_workbook_extensions(extension_list):
    for extension in extension_list:
        uri = extension.get("uri", "").upper()
        if extension.tag == MAIN + "ext" and len(extension) == 1:
            child = extension[0]
            # Chart options have no effect in books that pass our chart check.
            if (uri == CHART_TRACKING_EXT and child.tag == X15 + "workbookPr"
                    and not len(child) and child.keys() == ["chartTrackingRefBase"]):
                continue
            # Excel can rebuild calculation-engine metadata on open.
            if (uri == CALC_FEATURES_EXT and child.tag == CALC_FEATURES + "calcFeatures"
                    and not child.attrib and all(
                        feature.tag == CALC_FEATURES + "feature" and not len(feature)
                        and feature.keys() == ["name"] for feature in child)):
                continue
        suffix = f" {uri}" if re.fullmatch(r"\{[A-F0-9-]{36}\}", uri) else ""
        raise WorkbookError(f"Неподдерживаемое расширение книги Excel{suffix}.")


def inspect_package(data):
    if len(data) > MAX_FILE:
        raise WorkbookError("Размер книги превышает 20 МБ.")
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if (len(entries) > 2_000 or len(names) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_EXPANDED):
            raise WorkbookError("Распакованная книга превышает допустимый размер.")
        if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
            raise WorkbookError("Выберите корректную книгу .xlsx.")
        for name in names:
            if not UNSUPPORTED.search(name):
                continue
            if re.fullmatch(r"xl/drawings/[^/]+\.vml", name):
                with archive.open(name) as stream:
                    # Excel stores legacy notes as VML shapes. Other VML shapes are lost on save.
                    for _, element in iterparse(stream, events=("end",)):
                        if element.tag == VML_SHAPE:
                            client = element.find(VML_CLIENT)
                            if client is None or client.attrib.get("ObjectType") != "Note":
                                raise WorkbookError("Книга содержит фигуры или элементы управления Excel.")
                            element.clear()
                continue
            raise WorkbookError("Книга содержит неподдерживаемые элементы, например рисунки, диаграммы, сводные таблицы или подключения.")
        with archive.open("xl/workbook.xml") as stream:
            extensions = None
            for event, element in iterparse(stream, events=("start", "end")):
                if event == "start":
                    if element.tag == MAIN + "extLst" and extensions is None:
                        extensions = element
                    continue
                if element is extensions:
                    check_workbook_extensions(element)
                    extensions = None
                if extensions is None:
                    element.clear()
        cells = 0
        merged = 0
        for name in sorted(names):
            if not re.fullmatch(r"xl/worksheets/[^/]+\.xml", name):
                continue
            with archive.open(name) as stream:
                stack = []
                for event, element in iterparse(stream, events=("start", "end")):
                    if event == "start":
                        stack.append(element)
                        continue
                    if element.tag in {MAIN + "extLst", MAIN + "sheetProtection", MAIN + "oleObjects",
                                       MAIN + "controls", MAIN + "legacyDrawingHF"}:
                        raise WorkbookError("В книге есть защита листа или неподдерживаемые элементы Excel.")
                    if element.tag == MAIN + "c":
                        cells += 1
                        if cells > MAX_CELLS:
                            raise WorkbookError("В книге больше 300 000 ячеек с данными или оформлением.")
                    if element.tag == MAIN + "mergeCell":
                        left, top, right, bottom = range_boundaries(element.attrib["ref"])
                        if not (1 <= top <= bottom <= MAX_ROWS and 1 <= left <= right <= MAX_COLS):
                            raise WorkbookError("Недопустимый диапазон объединения.")
                        merged += (right - left + 1) * (bottom - top + 1)
                        if merged > MAX_CELLS:
                            raise WorkbookError("Объединённые области книги слишком велики.")
                    stack.pop()
                    if stack:
                        stack[-1].remove(element)
                    element.clear()


def open_excel(data):
    try:
        inspect_package(data)
        book = load_workbook(BytesIO(data), keep_links=True, data_only=False, rich_text=True)
    except WorkbookError:
        raise
    except (BadZipFile, OSError, ValueError, SyntaxError, KeyError, IndexError, TypeError, RuntimeError, AttributeError, DefusedXmlException) as exc:
        raise WorkbookError("Не удалось прочитать книгу .xlsx.") from exc
    if not any(sheet.sheet_state == "visible" for sheet in book.worksheets):
        book.close()
        raise WorkbookError("В книге нет видимых листов.")
    return book


def visible_sheets(book):
    return [(index, sheet) for index, sheet in enumerate(book.worksheets)
            if sheet.sheet_state == "visible"]


def worksheet_info(book):
    return [{"id": index, "name": sheet.title, "rows": sheet.max_row, "columns": sheet.max_column}
            for index, sheet in visible_sheets(book)]


def display_cell(cell):
    value = cell.value
    if cell.data_type == "f":
        return {"text": str(getattr(value, "text", value) or "Формула"), "kind": "formula"}
    if isinstance(value, (date, time)):
        return {"text": value.isoformat(), "kind": "date"}
    if isinstance(value, timedelta):
        return {"text": str(value), "kind": "date"}
    if type(value) is bool:
        return {"text": "TRUE" if value else "FALSE", "kind": "boolean"}
    kind = "error" if cell.data_type == "e" else "number" if isinstance(value, (float, int)) else "text"
    return {"text": str(value), "kind": kind}


def cells_window(book, sheet_id, row, column, rows, columns):
    sheets = dict(visible_sheets(book))
    if sheet_id not in sheets:
        raise WorkbookError("Лист недоступен.")
    sheet = sheets[sheet_id]
    cells = []
    # Using existing cell records keeps window reads non-mutating, including blank cells.
    last_row = min(row + rows, MAX_ROWS + 1)
    last_column = min(column + columns, MAX_COLS + 1)
    for row_id in range(row, last_row):
        for col_id in range(column, last_column):
            cell = sheet._cells.get((row_id, col_id))
            if cell is not None and cell.value is not None:
                cells.append({"row": row_id, "column": col_id, **display_cell(cell)})
    return cells


def selection(book, areas, restore):
    visible = dict(visible_sheets(book))
    grouped = {}
    for area in areas:
        if area.sheet not in visible:
            raise WorkbookError("Выделение ссылается на недоступный лист.")
        grouped.setdefault(area.sheet, []).append(area)
    if not grouped and not restore:
        raise WorkbookError("Выберите ячейки, диапазоны, строки или столбцы.")
    return grouped


def plain_text(cell):
    return cell.data_type in {"s", "inlineStr"} and isinstance(cell.value, (str, CellRichText))


def numeric_cell(cell):
    return cell.data_type == "n" and type(cell.value) in (int, float)


def auto_pairs(book, areas, pairs):
    pairs = validate_pairs(pairs, allow_empty=True)
    selected = selection(book, areas, False)
    originals = {pair[0] for pair in pairs if len(pair) == 2}
    numeric_originals = {parse_number(pair[0]) for pair in pairs if len(pair) == 3}
    used = {code for pair in pairs if len(pair) == 2 for part in pair for code in AUTO_TOKEN.findall(part)}
    occupied_numbers = numeric_originals | {parse_number(pair[1]) for pair in pairs if len(pair) == 3}
    # Never issue a code that already occurs in workbook text or an existing rule.
    for sheet in book.worksheets:
        for cell in sheet._cells.values():
            if plain_text(cell):
                used.update(AUTO_TOKEN.findall(str(cell.value)))
            elif numeric_cell(cell):
                occupied_numbers.add(cell.value)
    counter, skipped = 1, 0
    for index, sheet in visible_sheets(book):
        if index not in selected:
            continue
        for _, cell in sorted(sheet._cells.items()):
            if not any(area.contains(cell.row, cell.column) for area in selected[index]):
                continue
            value = str(cell.value)
            if numeric_cell(cell):
                if cell.value in numeric_originals:
                    continue
                numeric_originals.add(cell.value)
                try:
                    parse_number(value)
                except WorkbookError:
                    skipped += 1
                    continue
                if len(pairs) == MAX_PAIRS:
                    skipped += 1
                    continue
                substitute = 1_000_000 + randbelow(9_000_000)
                while substitute in occupied_numbers:
                    substitute = 1_000_000 + randbelow(9_000_000)
                occupied_numbers.add(substitute)
                pairs.append((value, str(substitute), "number"))
                continue
            if not plain_text(cell):
                continue
            if not value.strip() or value in originals:
                continue
            originals.add(value)
            if (len(value) > MAX_PAIR_LENGTH or len(pairs) == MAX_PAIRS
                    or ILLEGAL_CHARACTERS_RE.search(value)):
                skipped += 1
                continue
            token = f"⟦PM_{counter:04d}⟧"
            while token in used:
                counter += 1
                token = f"⟦PM_{counter:04d}⟧"
            used.add(token)
            pairs.append((value, token))
    return {"pairs": [pair_data(pair) for pair in pairs], "skipped": skipped}


def set_text(cell, text):
    cell.value = text
    cell.data_type = "s"


def process_book(book, pairs, areas, restore=False):
    matcher = Matcher(pairs)
    number_map = matcher.reverse_numbers if restore else matcher.numbers
    selected = selection(book, areas, restore)
    examples, warnings, unknown, count = [], {}, set(), 0
    output_text_size = 0

    def warn(code, location):
        item = warnings.setdefault(code, {"code": code, "count": 0, "examples": []})
        item["count"] += 1
        if len(item["examples"]) < 8:
            item["examples"].append(location)

    for index, sheet in enumerate(book.worksheets):
        visible = sheet.sheet_state == "visible"
        if not restore:
            if not visible:
                warn("hidden_sheet", sheet.title)
            for dimension in sheet.row_dimensions.values():
                if dimension.hidden:
                    warn("hidden_row", f"{sheet.title}!{dimension.index}")
            for dimension in sheet.column_dimensions.values():
                if dimension.hidden:
                    warn("hidden_column", f"{sheet.title}!{dimension.index}")
            if matcher.pattern.search(sheet.title):
                warn("original_outside", sheet.title)
        for cell in sheet._cells.values():
            location = None
            if not restore and (cell.data_type == "f" or cell.comment or cell.hyperlink):
                location = f"{sheet.title}!{cell.coordinate}"
                if cell.data_type == "f":
                    warn("formula", location)
                if cell.comment:
                    warn("comment", location)
                if cell.hyperlink:
                    warn("hyperlink", location)
            is_number = numeric_cell(cell)
            if not plain_text(cell) and not (is_number and number_map):
                continue
            value = cell.value if is_number else str(cell.value)
            in_scope = visible and (not selected or any(
                area.contains(cell.row, cell.column) for area in selected.get(index, ())
            ))
            replacement = value
            if is_number:
                if in_scope:
                    replacement = number_map.get(value, value)
                    if not restore and matcher.reverse_numbers.get(replacement, replacement) != value:
                        raise WorkbookError(
                            f"{sheet.title}!{cell.coordinate}: числовая подстановка уже есть в выбранной области."
                        )
                elif not restore:
                    if value in matcher.numbers:
                        warn("original_outside", f"{sheet.title}!{cell.coordinate}")
                    if value in matcher.reverse_numbers:
                        warn("substitute_outside", f"{sheet.title}!{cell.coordinate}")
            elif in_scope:
                if restore and len(unknown) < 20:
                    unknown.update(matcher.unknown(value))
                try:
                    replacement, _ = matcher.replace(value, restore)
                    reversible = restore or matcher.replace(replacement, True)[0] == value
                except WorkbookError as exc:
                    raise WorkbookError(f"{sheet.title}!{cell.coordinate}: результат длиннее 32 767 символов.") from exc
                if not reversible:
                    raise WorkbookError(
                        f"{sheet.title}!{cell.coordinate}: обратная подмена неоднозначна. Проверьте правила или исходные коды."
                    )
            elif not restore:
                if matcher.pattern.search(value):
                    warn("original_outside", f"{sheet.title}!{cell.coordinate}")
                if matcher.reverse.search(value):
                    warn("substitute_outside", f"{sheet.title}!{cell.coordinate}")
            if not is_number:
                output_text_size += len(replacement.encode("utf-8"))
            if output_text_size > MAX_OUTPUT_TEXT:
                raise WorkbookError("Объём текста в результирующей книге слишком велик.")
            if replacement == value:
                continue
            if isinstance(cell.value, CellRichText):
                raise WorkbookError(f"{sheet.title}!{cell.coordinate}: подмена затрагивает форматирование внутри ячейки.")
            if is_number:
                cell.value = replacement
            else:
                set_text(cell, replacement)
            count += 1
            if len(examples) < 40:
                examples.append({"sheet": sheet.title, "address": cell.coordinate,
                                 "before": str(value)[:600],
                                 "after": (format(replacement, ".16g") if is_number else replacement)[:600]})
    if not restore:
        if book._external_links:
            warn("external_link", "Книга")
        for name in ("title", "subject", "creator", "lastModifiedBy", "description", "keywords", "category"):
            value = getattr(book.properties, name, None)
            if isinstance(value, str) and matcher.pattern.search(value):
                warn("metadata", "Свойства книги")
        for definition in book.defined_names.values():
            if matcher.pattern.search(definition.name) or matcher.pattern.search(definition.attr_text or ""):
                warn("metadata", f"Именованный диапазон «{definition.name}»")
        if book.custom_doc_props.names:
            warn("metadata", "Дополнительные свойства книги")
    output = BytesIO()
    if count:
        book.save(output)
        if output.tell() > MAX_FILE:
            raise WorkbookError("Результирующая книга превышает 20 МБ.")
    return output.getvalue(), {"changed": count, "examples": examples, "warnings": list(warnings.values()),
                               "unknown": sorted(unknown)[:20]}


def restore_text(text, pairs):
    matcher = Matcher(pairs)
    try:
        result, changed = matcher.replace(text, restore=True, limit=2_000_000)
    except WorkbookError as exc:
        raise WorkbookError("Восстановленный текст слишком велик.") from exc
    return {"text": result, "changed": changed, "unknown": matcher.unknown(text)}


def mask_text(text, pairs):
    matcher = Matcher(pairs)
    try:
        output, changed = matcher.replace(text, limit=2_000_000)
        reversible = not changed or matcher.replace(output, restore=True, limit=2_000_000)[0] == text
    except WorkbookError as exc:
        raise WorkbookError("Результат подмены слишком велик.") from exc
    if not reversible:
        raise WorkbookError("Обратная подмена неоднозначна. Выберите коды, которых нет в исходном тексте.")
    return {"text": output, "changed": changed}
