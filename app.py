from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

import streamlit as st
from defusedxml.common import DefusedXmlException
from openpyxl.utils.exceptions import InvalidFileException

from excel import process_workbook


st.set_page_config(page_title="Подмена")
st.title("Подмена")
st.caption("Замените заданные значения в Excel или восстановите их по той же матрице.")
mode = st.radio("Режим", ["Подменить", "Восстановить"], horizontal=True)
workbook = st.file_uploader("Книга Excel", type=["xlsx"])
st.subheader("Матрица замен")
st.caption("Совпадения учитывают регистр. Сохраните эту матрицу для восстановления.")
rows = st.data_editor(
    [{"Исходное значение": "", "Подстановка": ""}],
    num_rows="dynamic",
    hide_index=True,
)
st.caption(
    "Результат содержит значения ячеек видимых листов. Формулы, оформление, "
    "имена листов, комментарии и вложенные объекты исключаются."
)

if st.button("Обработать", disabled=workbook is None):
    pairs = [
        (row["Исходное значение"] or "", row["Подстановка"] or "")
        for row in rows
        if row["Исходное значение"] or row["Подстановка"]
    ]
    restore = mode == "Восстановить"
    try:
        output, changed = process_workbook(workbook.getvalue(), pairs, restore)
    except ValueError as error:
        st.error(str(error))
    except (BadZipFile, InvalidFileException, KeyError, OSError, ParseError, DefusedXmlException):
        st.error("Не удалось прочитать книгу. Загрузите корректный файл .xlsx.")
    else:
        st.success(f"Изменено ячеек — {changed}")
        st.download_button(
            "Скачать Excel",
            output,
            file_name="restored.xlsx" if restore else "masked.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
