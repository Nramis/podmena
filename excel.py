import re
from io import BytesIO

from openpyxl import Workbook, load_workbook


def replace_text(value, pattern, mapping):
    return pattern.sub(lambda match: mapping[match.group()], value)


def process_workbook(data, pairs, restore=False):
    originals = dict(pairs)
    tokens = {token: original for original, token in pairs}
    if not pairs or any(not original or not token for original, token in pairs):
        raise ValueError("Заполните обе колонки матрицы.")
    if len(originals) != len(pairs) or len(tokens) != len(pairs):
        raise ValueError("Исходные значения и подстановки должны быть уникальными.")

    forward, backward = (tokens, originals) if restore else (originals, tokens)
    pattern, reverse_pattern = (
        re.compile("|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True)))
        for mapping in (forward, backward)
    )

    source = load_workbook(BytesIO(data), read_only=True, keep_links=False)
    result = Workbook()
    result.remove(result.active)
    changed = 0
    try:
        for sheet in source.worksheets:
            if sheet.sheet_state != "visible":
                continue
            target = result.create_sheet(f"Лист {len(result.worksheets) + 1}")
            for row in sheet.iter_rows():
                for cell in row:
                    value = cell.value
                    if value is None or cell.data_type == "f":
                        continue
                    if isinstance(value, str):
                        replacement = replace_text(value, pattern, forward)
                        if not restore and replace_text(replacement, reverse_pattern, backward) != value:
                            raise ValueError(
                                f"Подстановку в ячейке {cell.coordinate} нельзя однозначно "
                                "восстановить. Выберите другие подстановки."
                            )
                        if len(replacement) > 32767:
                            raise ValueError(f"Результат в ячейке {cell.coordinate} превышает лимит Excel.")
                        changed += replacement != value
                        target.cell(cell.row, cell.column, replacement).data_type = "s"
                    else:
                        target.cell(cell.row, cell.column, value)
        if not result.worksheets:
            raise ValueError("В книге нет видимых листов с ячейками.")
        output = BytesIO()
        result.save(output)
        return output.getvalue(), changed
    finally:
        source.close()
        result.close()
