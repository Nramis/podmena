import asyncio
import logging
import os
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import timedelta, timezone
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Annotated
from urllib.parse import quote

from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, File, HTTPException, Path as PathParam, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select, text
from starlette.concurrency import run_in_threadpool

from excel import MAX_COLS, MAX_FILE, MAX_ROWS, WorkbookError, auto_pairs, cells_window, mask_text, process_book, restore_text
from .db import Cipher, History, LoginSession, Matrix, User, Version, make_sessions, new_id, passwords, secret
from .db import token_hash, utcnow
from .documents import MAX_DOCUMENT_BYTES, MAX_TEXT, read_document
from .library import check_draft, get_version, owned_matrix, public_matrix, read_pairs, recent_history
from .library import record, reference, save_draft, version_data
from .schemas import ArchiveInput, Credentials, DownloadInput, Identifier, ImportInput, MatrixDraft
from .schemas import MaskTextInput, MatrixRef, PreviewInput, SelectionInput, TextInput, pair_values
from .workspace import PREVIEW_TTL, Preview, Workspace, source_book

COOKIE = "podmena_session"
SESSION_SECONDS = 8 * 60 * 60
EXCEL_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LOG = logging.getLogger(__name__)


def attachment(content, media_type, name):
    return Response(content, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
    })


class LoginThrottle:
    def __init__(self):
        self.lock = Lock()
        self.attempts = OrderedDict()
        self.hash_slots = BoundedSemaphore(4)

    def check(self, address, name):
        now = monotonic()
        with self.lock:
            for key, limit in ((("address", address), 100), (("username", name), 10)):
                deadline, attempts = self.attempts.pop(key, (0, 0))
                if deadline <= now:
                    deadline, attempts = now + 300, 0
                self.attempts[key] = deadline, attempts + 1
                if len(self.attempts) > 10_000:
                    self.attempts.popitem(last=False)
                if attempts >= limit:
                    raise HTTPException(429, "Слишком много попыток входа. Повторите через несколько минут.")


def create_app(sessions=None, cipher=None, secure_cookie=None, static_dir=None):
    work_slots = BoundedSemaphore(2)
    throttle = LoginThrottle()
    display_cache = OrderedDict()
    cache_lock = Lock()

    @contextmanager
    def work_slot():
        if not work_slots.acquire(blocking=False):
            raise HTTPException(503, "Сервис занят обработкой книг. Попробуйте ещё раз.")
        try:
            yield
        finally:
            work_slots.release()

    def read_window(entry, sheet, row, column, rows, columns):
        with cache_lock:
            book = display_cache.pop(entry.id, None)
            if book is not None:
                display_cache[entry.id] = book
        if book is None:
            with work_slot():
                book = source_book(entry)
            with cache_lock:
                display_cache[entry.id] = book
                if len(display_cache) > 2:
                    _, oldest = display_cache.popitem(last=False)
                    oldest.close()
        return cells_window(book, sheet, row, column, rows, columns)

    def forget_book(entry_id):
        with cache_lock:
            book = display_cache.pop(entry_id, None)
            if book is not None:
                book.close()

    def clean(app):
        app.state.workspace.clean()
        with app.state.workspace.lock:
            active = set(app.state.workspace.entries)
        with cache_lock:
            for entry_id in list(display_cache):
                if entry_id not in active:
                    display_cache.pop(entry_id).close()
        with app.state.sessions.begin() as db:
            now = utcnow()
            db.execute(delete(LoginSession).where(LoginSession.expires <= now))
            db.execute(delete(History).where(History.created < now - timedelta(days=90)))

    async def cleanup_loop(app):
        while True:
            try:
                await run_in_threadpool(clean, app)
            except Exception as exc:
                LOG.error("Ошибка фоновой очистки (%s)", type(exc).__name__)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        app.state.sessions = sessions or make_sessions()
        app.state.cipher = cipher or Cipher(secret("MATRIX_KEY"))
        app.state.secure_cookie = (os.getenv("SECURE_COOKIE", "1") != "0"
                                   if secure_cookie is None else secure_cookie)
        app.state.workspace = Workspace()
        app.state.dummy_hash = passwords.hash(secrets.token_hex(32))
        task = asyncio.create_task(cleanup_loop(app))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await run_in_threadpool(app.state.workspace.close)
            with cache_lock:
                for book in display_cache.values():
                    book.close()
                display_cache.clear()
            if sessions is None:
                app.state.sessions.kw["bind"].dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def api_guard(request, call_next):
        if request.url.path.startswith("/api/"):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                if (request.headers.get("X-Podmena") != "1"
                        or request.headers.get("Sec-Fetch-Site") == "cross-site"):
                    return JSONResponse({"detail": "Запрос отклонён."}, 403)
                length = request.headers.get("content-length", "")
                limit = MAX_FILE + 64 * 1024 if request.url.path == "/api/books" else 13 * 1024 * 1024
                if length and (not length.isascii() or not length.isdecimal() or len(length) > 10):
                    return JSONResponse({"detail": "Некорректный размер запроса."}, 400)
                if length and int(length) > limit:
                    return JSONResponse({"detail": "Размер запроса превышает допустимый."}, 413)
                if not length and request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "Укажите размер запроса."}, 411)
            try:
                response = await call_next(request)
            except Exception as exc:
                LOG.error("Ошибка API (%s)", type(exc).__name__)
                response = JSONResponse({"detail": "Не удалось обработать запрос. Попробуйте ещё раз."}, 500)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def bad_input(_request, error):
        issues = error.errors()
        field = ".".join(str(part) for part in issues[0]["loc"][1:]) if issues else ""
        return JSONResponse({"detail": f"Проверьте поле «{field}»." if field else "Проверьте данные запроса."}, 422)

    @app.exception_handler(WorkbookError)
    async def bad_workbook(_request, error):
        return JSONResponse({"detail": str(error)}, 422)

    def database(request: Request):
        with request.app.state.sessions() as db:
            yield db

    def identify(request: Request, db=Depends(database)):
        token = request.cookies.get(COOKIE, "")
        if len(token) != 43:
            raise HTTPException(401, "Войдите в систему.")
        token_id = token_hash(token)
        login = db.get(LoginSession, token_id)
        if login is None or login.expires.replace(tzinfo=timezone.utc) <= utcnow():
            raise HTTPException(401, "Сеанс истёк. Войдите снова.")
        user = db.get(User, login.owner)
        if user is None or not user.active:
            raise HTTPException(401, "Учётная запись недоступна.")
        return user, token_id

    def book_entry(book_id: Identifier, request: Request, identity=Depends(identify)):
        user, session = identity
        return request.app.state.workspace.get(book_id, user.id, session)

    @app.get("/api/health")
    def health(request: Request):
        try:
            with request.app.state.sessions() as db:
                db.execute(text("SELECT 1 FROM users LIMIT 1"))
            return {"status": "ok"}
        except Exception:
            raise HTTPException(503, "База данных недоступна.")

    @app.get("/api/me")
    def me(identity=Depends(identify)):
        return {"username": identity[0].username}

    @app.post("/api/login")
    def login(data: Credentials, request: Request, response: Response, db=Depends(database)):
        name = data.username.lower()
        address = request.client.host if request.client else "unknown"
        if os.getenv("TRUST_PROXY") == "1":
            address = request.headers.get("X-Real-IP", address)[:64]
        throttle.check(address, name)
        if not throttle.hash_slots.acquire(blocking=False):
            raise HTTPException(429, "Сервис входа занят. Попробуйте ещё раз.")
        try:
            user = db.scalar(select(User).where(User.username == name))
            hashed = user.password_hash if user else request.app.state.dummy_hash
            try:
                passwords.verify(hashed, data.password)
            except VerificationError:
                raise HTTPException(401, "Неверный логин или пароль.")
            if user is None or not user.active:
                raise HTTPException(401, "Неверный логин или пароль.")
            db.refresh(user, with_for_update=True)
            if not user.active or user.password_hash != hashed:
                raise HTTPException(401, "Войдите с актуальными данными.")
            if passwords.check_needs_rehash(hashed):
                user.password_hash = passwords.hash(data.password)
            previous = request.cookies.get(COOKIE, "")
            previous_id = None
            if len(previous) == 43:
                previous_id = token_hash(previous)
                db.execute(delete(LoginSession).where(LoginSession.id == previous_id))
            token = secrets.token_urlsafe(32)
            db.add(LoginSession(id=token_hash(token), owner=user.id,
                                expires=utcnow() + timedelta(seconds=SESSION_SECONDS)))
            db.commit()
        finally:
            throttle.hash_slots.release()
        if previous_id:
            for entry_id in request.app.state.workspace.close_session(previous_id):
                forget_book(entry_id)
        response.set_cookie(COOKIE, token, httponly=True, secure=request.app.state.secure_cookie,
                            samesite="strict", max_age=SESSION_SECONDS)
        return {"username": user.username}

    @app.post("/api/logout", status_code=204)
    def logout(request: Request, response: Response, db=Depends(database), identity=Depends(identify)):
        _, session = identity
        db.execute(delete(LoginSession).where(LoginSession.id == session))
        db.commit()
        for entry_id in request.app.state.workspace.close_session(session):
            forget_book(entry_id)
        response.delete_cookie(COOKIE, httponly=True, secure=request.app.state.secure_cookie, samesite="strict")

    @app.get("/api/matrices")
    def matrices(db=Depends(database), identity=Depends(identify)):
        result = db.scalars(select(Matrix).where(Matrix.owner == identity[0].id).order_by(Matrix.name))
        return [public_matrix(matrix) for matrix in result]

    @app.post("/api/matrices", status_code=201)
    def save_matrix(draft: MatrixDraft, request: Request, db=Depends(database), identity=Depends(identify)):
        cipher = request.app.state.cipher
        matrix, version = save_draft(db, cipher, identity[0].id, draft)
        db.commit()
        return version_data(cipher, matrix, version)

    @app.post("/api/matrices/import", status_code=201)
    def import_matrix(data: ImportInput, request: Request, db=Depends(database), identity=Depends(identify)):
        return save_matrix(MatrixDraft(name=data.name, pairs=data.pairs), request, db, identity)

    @app.get("/api/matrices/{matrix_id}/versions")
    def versions(matrix_id: Identifier, db=Depends(database), identity=Depends(identify)):
        matrix = owned_matrix(db, identity[0].id, matrix_id)
        result = db.execute(select(Version.number, Version.created).where(Version.matrix_id == matrix.id)
                            .order_by(Version.number.desc()))
        return [{"version": number, "created": created} for number, created in result]

    @app.get("/api/matrices/{matrix_id}/versions/{number}")
    def read_version(matrix_id: Identifier, number: Annotated[int, PathParam(gt=0)], request: Request,
                     db=Depends(database), identity=Depends(identify)):
        matrix, version = get_version(db, identity[0].id, MatrixRef(id=matrix_id, version=number))
        return version_data(request.app.state.cipher, matrix, version)

    @app.get("/api/matrices/{matrix_id}/versions/{number}/export")
    def export_matrix(matrix_id: Identifier, number: Annotated[int, PathParam(gt=0)], request: Request,
                      db=Depends(database), identity=Depends(identify)):
        version = read_version(matrix_id, number, request, db, identity)
        content = {key: version[key] for key in ("name", "pairs", "version")}
        content["format"] = "podmena-matrix-v1"
        return JSONResponse(content, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote('matrix-v' + str(number) + '.json')}",
        })

    @app.patch("/api/matrices/{matrix_id}")
    def archive_matrix(matrix_id: Identifier, data: ArchiveInput, db=Depends(database), identity=Depends(identify)):
        matrix = owned_matrix(db, identity[0].id, matrix_id, lock=True)
        matrix.archived = data.archived
        db.commit()
        return public_matrix(matrix)

    @app.get("/api/history")
    def history(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 30,
                offset: Annotated[int, Query(ge=0)] = 0, db=Depends(database), identity=Depends(identify)):
        return recent_history(db, request.app.state.cipher, identity[0].id, limit, offset)

    @app.post("/api/books", status_code=201)
    def upload(request: Request, file: Annotated[UploadFile, File()], identity=Depends(identify)):
        user, session = identity
        name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        name = "".join(character for character in name if character.isprintable())[-180:]
        if not name.lower().endswith(".xlsx"):
            raise HTTPException(422, "Выберите файл Excel с расширением .xlsx.")
        with work_slot():
            data = file.file.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise HTTPException(413, "Размер книги превышает 20 МБ.")
            result, previous = request.app.state.workspace.put(user.id, session, name, data)
        for entry_id in previous:
            forget_book(entry_id)
        return result

    @app.delete("/api/books/{book_id}", status_code=204)
    def close_book(request: Request, entry=Depends(book_entry)):
        request.app.state.workspace.remove(entry)
        forget_book(entry.id)

    @app.get("/api/books/{book_id}/sheets/{sheet}/cells")
    def window(sheet: int, entry=Depends(book_entry), row: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 1,
               column: Annotated[int, Query(ge=1, le=MAX_COLS)] = 1,
               rows: Annotated[int, Query(ge=1, le=100)] = 48,
               columns: Annotated[int, Query(ge=1, le=MAX_COLS)] = 12):
        if rows * columns > 2400:
            raise HTTPException(422, "Запрошено слишком много ячеек.")
        return read_window(entry, sheet, row, column, rows, columns)

    @app.post("/api/books/{book_id}/auto-pairs")
    def generate(data: SelectionInput, entry=Depends(book_entry)):
        with entry.lock, work_slot(), closing(source_book(entry)) as book:
            return auto_pairs(book, [region.area() for region in data.areas],
                              pair_values(data.pairs))

    @app.post("/api/books/{book_id}/preview")
    def preview(data: PreviewInput, request: Request, entry=Depends(book_entry), db=Depends(database)):
        cipher = request.app.state.cipher
        if data.mode == "mask":
            _, pairs = check_draft(db, entry.owner, data.draft)
        else:
            _, version = get_version(db, entry.owner, data.matrix)
            pairs = read_pairs(cipher, version)
        db.rollback()
        with entry.lock, work_slot(), closing(source_book(entry)) as book:
            output, summary = process_book(book, pairs, [region.area() for region in data.areas],
                                           restore=data.mode == "restore")
            new_preview = new_id()
            path = entry.source.parent / f"{new_preview}.xlsx"
            if summary["changed"]:
                try:
                    path.write_bytes(output)
                except OSError:
                    path.unlink(missing_ok=True)
                    raise HTTPException(503, "Не удалось сохранить результат. Попробуйте ещё раз.")
            if entry.preview:
                entry.preview.result.unlink(missing_ok=True)
            entry.preview = Preview(new_preview, path, data, summary)
            return {"id": new_preview, **summary}

    @app.post("/api/books/{book_id}/previews/{preview_id}/download")
    def download(preview_id: Identifier, data: DownloadInput, request: Request,
                 entry=Depends(book_entry), db=Depends(database)):
        cipher = request.app.state.cipher
        with entry.lock:
            prepared = entry.preview
            if prepared is None or prepared.id != preview_id or monotonic() - prepared.touched > PREVIEW_TTL:
                raise HTTPException(409, "Предпросмотр устарел. Запустите его снова.")
            if not prepared.summary["changed"]:
                raise HTTPException(422, "В выбранной области нет изменений.")
            if prepared.summary["warnings"] and not data.acknowledged:
                raise HTTPException(409, "Подтвердите предупреждения перед скачиванием.")
            try:
                output = prepared.result.read_bytes()
            except OSError:
                raise HTTPException(503, "Результат недоступен. Запустите предпросмотр снова.")
            event = db.get(History, prepared.id)
            if event is None:
                if prepared.request.mode == "mask":
                    matrix, version = save_draft(db, cipher, entry.owner, prepared.request.draft)
                    ref = reference(matrix, version)
                else:
                    ref = prepared.request.matrix
                record(db, cipher, entry.owner, prepared.request.mode, ref, prepared.summary["changed"],
                       data.label, operation_id=prepared.id)
                db.commit()
            else:
                ref = MatrixRef(id=event.matrix_id, version=event.version)
            prepared.touched = monotonic()
            suffix = "подмена" if prepared.request.mode == "mask" else "восстановлено"
            response = attachment(output, EXCEL_TYPE, f"{entry.name[:-5]} — {suffix}.xlsx")
            response.headers["X-Matrix-Id"] = ref.id
            response.headers["X-Matrix-Version"] = str(ref.version)
            return response

    @app.post("/api/documents/text")
    def extract_document(file: Annotated[UploadFile, File()], _identity=Depends(identify)):
        with work_slot():
            data = file.file.read(MAX_DOCUMENT_BYTES + 1)
            if len(data) > MAX_DOCUMENT_BYTES:
                raise HTTPException(413, "Файл превышает 10 МБ.")
            return {"text": read_document(file.filename or "", data)}


    @app.post("/api/mask/text")
    def prepare_text(data: MaskTextInput, request: Request, db=Depends(database), identity=Depends(identify)):
        owner = identity[0].id
        cipher = request.app.state.cipher
        with work_slot():
            result = mask_text(data.text, pair_values(data.draft.pairs))
        if len(result["text"]) > MAX_TEXT:
            raise WorkbookError("После подмены получилось больше 250 000 символов. Сократите текст или правила.")
        if not result["changed"] or data.preview:
            return result
        matrix, version = save_draft(db, cipher, owner, data.draft)
        ref = reference(matrix, version)
        record(db, cipher, owner, "mask_text", ref, result["changed"], data.label)
        db.commit()
        return {**result, "matrix": ref}


    @app.post("/api/restore/text")
    def recover_text(data: TextInput, request: Request, db=Depends(database), identity=Depends(identify)):
        owner = identity[0].id
        cipher = request.app.state.cipher
        _, version = get_version(db, owner, data.matrix)
        with work_slot():
            result = restore_text(data.text, read_pairs(cipher, version))
        if result["changed"]:
            record(db, cipher, owner, "text", data.matrix, result["changed"], data.label)
            db.commit()
        return result

    assets = Path(static_dir or os.getenv("STATIC_DIR", "frontend/dist"))
    if (assets / "index.html").is_file():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    return app


app = create_app()
