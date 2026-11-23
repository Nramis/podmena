import os
import unittest
from unittest.mock import patch
from contextlib import closing
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart
from openpyxl.comments import Comment
from openpyxl.styles import Font
from sqlalchemy import inspect, select
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject

from excel import Area, WorkbookError, auto_pairs, open_excel, process_book, restore_text
from server.documents import read_document
from server.db import Base, Cipher, History, User, Version, make_sessions, passwords, utcnow
from server.main import create_app

PASSWORD = "example-test-password"
HEADERS = {"X-Podmena": "1"}
RULE = {"original": "Анна Петрова", "substitute": "⟦PM_0001⟧"}
FILE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def excel_file(reply=False, unsupported=False):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ответ" if reply else "Клиенты"
    if reply:
        sheet["B7"] = "Согласовано для ⟦PM_0001⟧"
    else:
        sheet["A1"] = "Анна Петрова"
        sheet["A2"] = "Сотрудник: Анна Петрова"
        sheet["A2"].font = Font(bold=True, color="006122")
        sheet["A2"].comment = Comment("Тестовый комментарий", "Тест")
        sheet["B2"] = "=2+2"
        sheet["C2"] = date(2025, 10, 1)
        sheet["D2"] = 17
        sheet["A4"] = "Анна Петрова"
        sheet["A6"] = "#REF!"
        sheet.row_dimensions[4].hidden = True
        sheet.column_dimensions["C"].hidden = True
        sheet.merge_cells("B5:D5")
        second = workbook.create_sheet("Итоги")
        second["C2"] = "Анна Петрова"
        hidden = workbook.create_sheet("Черновик")
        hidden.sheet_state = "hidden"
        hidden["A1"] = "Анна Петрова"
    if unsupported:
        sheet.add_chart(BarChart(), "F1")
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class ExcelTests(unittest.TestCase):
    def test_ranges_warnings_and_structure(self):
        with closing(open_excel(excel_file())) as book:
            output, summary = process_book(book, [tuple(RULE.values())],
                                           [Area(0, 1, 1, 2, 1), Area(0, 2, 1, 2, 4), Area(1, 2, 3, 2, 3)])
        self.assertEqual(summary["changed"], 3)
        self.assertEqual({value["code"] for value in summary["warnings"]},
                         {"hidden_sheet", "hidden_row", "hidden_column", "formula", "comment", "original_outside"})
        with closing(load_workbook(BytesIO(output))) as book:
            self.assertEqual(book.sheetnames, ["Клиенты", "Итоги", "Черновик"])
            sheet = book.worksheets[0]
            self.assertEqual(sheet["A2"].value, "Сотрудник: ⟦PM_0001⟧")
            self.assertTrue(sheet["A2"].font.bold)
            self.assertEqual(sheet["A2"].comment.text, "Тестовый комментарий")
            self.assertEqual(sheet["B2"].value, "=2+2")
            self.assertEqual(sheet["C2"].value.date(), date(2025, 10, 1))
            self.assertEqual(sheet["D2"].value, 17)
            self.assertEqual(sheet["A6"].data_type, "e")
            self.assertIn("B5:D5", sheet.merged_cells)
            self.assertTrue(sheet.row_dimensions[4].hidden)
            self.assertEqual(book["Черновик"]["A1"].value, RULE["original"])

    def test_auto_tokens_literal_matching_and_conflicts(self):
        with closing(open_excel(excel_file())) as book:
            areas = [Area(0, 1, 1, 2, 1)]
            pairs = auto_pairs(book, areas, [("Сотрудник", "⟦PM_0001⟧")])["pairs"]
            self.assertEqual(pairs[1], {"original": "Анна Петрова", "substitute": "⟦PM_0002⟧"})
            with self.assertRaisesRegex(WorkbookError, "неоднозначна"):
                process_book(book, [("Анна", "Петрова")], areas)
        with closing(open_excel(excel_file())) as book:
            output, _ = process_book(book, [("Анна Петрова", "=2+2")], [Area(0, 1, 1, 1, 1)])
        with closing(load_workbook(BytesIO(output))) as book:
            self.assertEqual(book.active["A1"].data_type, "s")
        result = restore_text("⟦PM_0001⟧ / ⟦PM_0022⟧", [tuple(RULE.values())])
        self.assertEqual(result, {"text": "Анна Петрова / ⟦PM_0022⟧", "unknown": ["⟦PM_0022⟧"], "changed": 1})

    def test_allows_excel_workbook_metadata_and_preserves_structure_lock(self):
        with closing(load_workbook(BytesIO(excel_file()))) as book:
            book.security.lockStructure = True
            book.security.set_workbook_password("test-only")
            source = BytesIO()
            book.save(source)
            expected_password = book.security.workbookPassword
        chart_extension = (
            '<ext uri="{140A7094-0E35-4892-8432-C4D2E57EDEB5}">'
            '<x15:workbookPr xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main"'
            ' chartTrackingRefBase="1"/></ext>'
        )
        calc_extension = (
            '<ext uri="{B58B0392-4F1F-4190-BB64-5DF3571DCE5F}">'
            '<calcFeatures xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/calcfeatures">'
            '<feature name="microsoft.com:RD"/></calcFeatures></ext>'
        )

        def inject_extension(extension):
            stream = BytesIO()
            with ZipFile(source) as original, ZipFile(stream, "w") as edited:
                for name in original.namelist():
                    contents = original.read(name)
                    if name == "xl/workbook.xml":
                        tail = f"<extLst>{extension}</extLst></workbook>".encode()
                        contents = contents.replace(b"</workbook>", tail)
                    edited.writestr(name, contents)
            return stream.getvalue()

        with closing(open_excel(inject_extension(chart_extension + calc_extension))) as book:
            output, summary = process_book(book, [tuple(RULE.values())], [Area(0, 1, 1, 1, 1)])
        self.assertEqual(summary["changed"], 1)
        with closing(load_workbook(BytesIO(output))) as book:
            self.assertTrue(book.security.lockStructure)
            self.assertEqual(book.security.workbookPassword, expected_password)
        with self.assertRaisesRegex(WorkbookError, "Неподдерживаемое расширение"):
            open_excel(inject_extension('<ext uri="{00000000-0000-0000-0000-000000000001}"><unknown/></ext>'))

    def test_bad_workbooks(self):
        for data in (b"invalid", excel_file(unsupported=True)):
            with self.assertRaises(WorkbookError):
                open_excel(data)
        with patch("excel.MAX_CELLS", 1), self.assertRaisesRegex(WorkbookError, "300 000"):
            open_excel(excel_file())
        invalid = BytesIO()
        with ZipFile(invalid, "w") as archive:
            archive.writestr("xl/workbook.xml", "broken")
            archive.writestr("[Content_Types].xml", "")
        with self.assertRaises(WorkbookError):
            open_excel(invalid.getvalue())


class DocumentTests(unittest.TestCase):
    def test_word_paragraphs_and_tables(self):
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>One</w:t></w:r><w:del><w:r><w:t>Deleted</w:t></w:r></w:del></w:p>"
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Two</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
                "</w:body></w:document>"
            ))
        self.assertEqual(read_document("text.docx", stream.getvalue()), "One\nTwo")

    def test_pdf_text_and_blank_page(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        blank = BytesIO()
        writer.write(blank)
        with self.assertRaisesRegex(WorkbookError, "нет доступного текста"):
            read_document("blank.pdf", blank.getvalue())
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }),
        })})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 10 Td (Test PDF) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        output = BytesIO()
        writer.write(output)
        self.assertEqual(read_document("text.pdf", output.getvalue()).strip(), "Test PDF")


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        default = "sqlite:///" + str(Path(cls.temp.name) / "test.db")
        cls.sessions = make_sessions(os.getenv("PODMENA_TEST_DATABASE_URL", default))
        cls.engine = cls.sessions.kw["bind"]
        if inspect(cls.engine).get_table_names():
            raise RuntimeError("Use an empty dedicated test database.")
        Base.metadata.create_all(cls.engine)
        with cls.sessions.begin() as db:
            for username in ("alice", "bob", "documents", "numbers"):
                db.add(User(username=username, password_hash=passwords.hash(PASSWORD)))
        cls.cipher = Cipher(Fernet.generate_key())

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(cls.engine)
        cls.engine.dispose()
        cls.temp.cleanup()

    @staticmethod
    def login(client, username):
        response = client.post("/api/login", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200, response.text

    def test_numeric_round_trip_and_matrix_export(self):
        with closing(Workbook()) as source:
            source.active.append([-12.5, "-12.5", -12.5, "=A1*2", 0])
            source.active["A1"].number_format = "0.00"
            content = BytesIO()
            source.save(content)
        areas = [{"sheet": 0, "top": 1, "bottom": 1, "left": 1, "right": 2}]
        numeric = {"original": "-12,5", "substitute": "0", "kind": "number"}
        app = create_app(self.sessions, self.cipher, secure_cookie=False)
        with TestClient(app, headers=HEADERS) as client:
            self.login(client, "numbers")

            def upload(data):
                response = client.post("/api/books", files={"file": ("числа.xlsx", data, FILE)})
                self.assertEqual(response.status_code, 201, response.text)
                return "/api/books/" + response.json()["id"]

            book = upload(content.getvalue())
            generated = client.post(book + "/auto-pairs", json={"areas": areas, "pairs": []})
            self.assertEqual(generated.status_code, 200, generated.text)
            number_pair, text_pair = generated.json()["pairs"]
            self.assertEqual(number_pair["kind"], "number")
            self.assertEqual(number_pair["original"], text_pair["original"])
            self.assertTrue(number_pair["substitute"].isdigit())

            draft = {"name": "Числовые пары", "pairs": [numeric]}
            request = {"mode": "mask", "areas": areas, "draft": draft}
            collision = {**request, "areas": [{**areas[0], "right": 5}]}
            self.assertEqual(client.post(book + "/preview", json=collision).status_code, 422)
            preview = client.post(book + "/preview", json=request)
            self.assertEqual(preview.status_code, 200, preview.text)
            summary = preview.json()
            self.assertEqual(summary["changed"], 1)
            warnings = {item["code"]: item["examples"] for item in summary["warnings"]}
            self.assertEqual(warnings["original_outside"], ["Sheet!C1"])
            self.assertEqual(warnings["substitute_outside"], ["Sheet!E1"])
            masked = client.post(f"{book}/previews/{summary['id']}/download", json={"acknowledged": True})
            self.assertEqual(masked.status_code, 200, masked.text)
            with closing(load_workbook(BytesIO(masked.content))) as result:
                sheet = result.active
                self.assertEqual(sheet["A1"].value, 0)
                self.assertEqual(sheet["A1"].data_type, "n")
                self.assertEqual(sheet["A1"].number_format, "0.00")
                self.assertEqual(sheet["B1"].value, "-12.5")
                self.assertEqual(sheet["C1"].value, -12.5)
                self.assertEqual(sheet["D1"].value, "=A1*2")

            ref = {"id": masked.headers["X-Matrix-Id"], "version": int(masked.headers["X-Matrix-Version"])}
            export = client.get(f"/api/matrices/{ref['id']}/versions/{ref['version']}/export").json()
            self.assertEqual(export["pairs"], [numeric])
            imported = client.post("/api/matrices/import", json={**export, "name": "Импорт чисел"})
            self.assertEqual(imported.status_code, 201, imported.text)
            self.assertEqual(imported.json()["pairs"], [numeric])

            reply = upload(masked.content)
            preview = client.post(reply + "/preview", json={"mode": "restore", "areas": areas, "matrix": ref})
            self.assertEqual(preview.status_code, 200, preview.text)
            restored = client.post(f"{reply}/previews/{preview.json()['id']}/download", json={})
            self.assertEqual(restored.status_code, 200, restored.text)
            with closing(load_workbook(BytesIO(restored.content))) as result:
                self.assertEqual(result.active["A1"].value, -12.5)
                self.assertEqual(result.active["E1"].value, 0)

    def test_import_preview_apply_and_restore_text(self):
        app = create_app(self.sessions, self.cipher, secure_cookie=False)
        with TestClient(app, headers=HEADERS) as client:
            self.login(client, "documents")
            response = client.post("/api/documents/text", files={"file": ("notes.md", "Анна\r\nАнна", "text/plain")})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"text": "Анна\nАнна"})
            draft = {"name": "Документы", "pairs": [{"original": "Анна", "substitute": "КОД"}]}
            data = {"text": response.json()["text"], "draft": draft}
            preview = client.post("/api/mask/text", json={**data, "preview": True})
            self.assertEqual(preview.json(), {"text": "КОД\nКОД", "changed": 2})
            self.assertEqual(client.get("/api/matrices").json(), [])
            collision = client.post("/api/mask/text", json={**data, "text": "Анна КОД"})
            self.assertEqual(collision.status_code, 422)
            result = client.post("/api/mask/text", json=data).json()
            self.assertEqual(client.get("/api/history").json()[0]["kind"], "mask_text")
            recovered = client.post("/api/restore/text", json={"text": result["text"], "matrix": result["matrix"]})
            self.assertEqual(recovered.json()["text"], data["text"])

    def test_team_workflow_and_restart(self):
        app = create_app(self.sessions, self.cipher, secure_cookie=False)
        with TestClient(app, headers=HEADERS) as alice:
            bob = TestClient(app, headers=HEADERS)
            self.addCleanup(bob.close)
            # Activate one shared lifespan when using two clients against the same app.
            self.login(alice, "alice")
            self.login(bob, "bob")
            uploaded = alice.post("/api/books", files={"file": ("отчёт.xlsx", excel_file(), FILE)})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            book = uploaded.json()["id"]
            cells = f"/api/books/{book}/sheets/0/cells"
            self.assertEqual(bob.get(cells).status_code, 404)
            self.assertEqual(alice.get(cells).json()[0]["text"], "Анна Петрова")
            preview_path = f"/api/books/{book}/preview"
            areas = [{"sheet": 0, "top": 1, "bottom": 2, "left": 1, "right": 1}]
            draft = {"name": "Тест клиентов", "pairs": [RULE]}
            preview = alice.post(preview_path, json={"mode": "mask", "areas": areas, "draft": draft})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["changed"], 2)
            url = f"/api/books/{book}/previews/{preview.json()['id']}/download"
            self.assertEqual(alice.post(url, json={}).status_code, 409)
            first = alice.post(url, json={"acknowledged": True, "label": "Тест договора"})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(alice.post(url, json={"acknowledged": True}).content, first.content)
            with closing(load_workbook(BytesIO(first.content))) as result:
                self.assertEqual(result.active["A1"].value, RULE["substitute"])
            history = alice.get("/api/history").json()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["label"], "Тест договора")
            ref = history[0]["matrix"]
            path = f"/api/matrices/{ref['id']}/versions/1"
            self.assertEqual(bob.get(path).status_code, 404)
            self.assertEqual(bob.get(path + "/export").status_code, 404)
            self.assertEqual(bob.get("/api/history").json(), [])
            self.assertEqual(alice.get(path + "/export").json()["pairs"], [RULE])
            self.assertEqual(alice.post("/api/matrices", json={**draft, "base": ref, "pairs": [
                {**RULE, "substitute": "НОВЫЙ_КОД"}
            ]}).json()["version"], 2)
            self.assertEqual(alice.post("/api/matrices", json={**draft, "base": ref}).status_code, 409)
            self.assertEqual(alice.patch(f"/api/matrices/{ref['id']}", json={"archived": True}).status_code, 200)
            restored = alice.post("/api/restore/text", json={"matrix": ref, "text": RULE["substitute"]})
            self.assertEqual(restored.json()["text"], RULE["original"])
            reply = alice.post("/api/books", files={"file": ("ответ.xlsx", excel_file(reply=True), FILE)}).json()
            self.assertEqual(alice.get(cells).status_code, 404)
            reply_id = reply["id"]
            preview = alice.post(f"/api/books/{reply_id}/preview", json={"mode": "restore", "areas": [], "matrix": ref})
            self.assertEqual(preview.status_code, 200, preview.text)
            response = alice.post(f"/api/books/{reply_id}/previews/{preview.json()['id']}/download", json={})
            self.assertEqual(response.status_code, 200, response.text)
            with closing(load_workbook(BytesIO(response.content))) as result:
                self.assertEqual(result.active["B7"].value, "Согласовано для Анна Петрова")
            self.assertEqual(alice.post("/api/logout", headers={"X-Podmena": "0"}).status_code, 403)
            self.assertEqual(alice.post("/api/logout").status_code, 204)
            self.assertEqual(alice.get("/api/me").status_code, 401)
        with self.sessions.begin() as db:
            self.assertNotIn(RULE["original"].encode(), db.get(Version, (ref["id"], 1)).payload)
            db.scalar(select(History).where(History.id == history[0]["id"])).created = utcnow() - timedelta(days=91)
        with TestClient(create_app(self.sessions, self.cipher, secure_cookie=False), headers=HEADERS) as client:
            self.login(client, "alice")
            self.assertEqual(client.get(path).json()["pairs"], [RULE])
            self.assertEqual(client.get(f"/api/books/{reply_id}/sheets/0/cells").status_code, 404)
            self.assertNotIn(history[0]["id"], [item["id"] for item in client.get("/api/history").json()])


if __name__ == "__main__":
    unittest.main()
