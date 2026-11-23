from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from excel import pair_data, validate_pairs
from .db import History, Matrix, Version, utcnow
from .schemas import MatrixRef, pair_values


def public_matrix(matrix):
    return {"id": matrix.id, "name": matrix.name, "latest": matrix.latest, "archived": matrix.archived}


def owned_matrix(db, owner, matrix_id, lock=False):
    query = select(Matrix).where(Matrix.id == matrix_id, Matrix.owner == owner)
    matrix = db.scalar(query.with_for_update() if lock else query)
    if matrix is None:
        raise HTTPException(404, "Матрица не найдена.")
    return matrix


def get_version(db, owner, ref):
    matrix = owned_matrix(db, owner, ref.id)
    version = db.get(Version, (matrix.id, ref.version))
    if version is None:
        raise HTTPException(404, "Версия матрицы не найдена.")
    return matrix, version


def read_pairs(cipher, version):
    return cipher.unpack(version.payload)["pairs"]


def version_data(cipher, matrix, version):
    payload = cipher.unpack(version.payload)
    return {"id": matrix.id, "version": version.number, "name": payload["name"],
            "created": version.created, "pairs": [pair_data(pair) for pair in payload["pairs"]]}


def check_draft(db, owner, draft, lock=False):
    pairs = validate_pairs(pair_values(draft.pairs))
    if draft.base:
        matrix = owned_matrix(db, owner, draft.base.id, lock)
        if matrix.latest != draft.base.version or matrix.archived:
            raise HTTPException(409, "Матрица изменилась или была архивирована. Обновите список или сохраните черновик как новую матрицу.")
        return matrix, pairs
    return None, pairs


def save_draft(db, cipher, owner, draft):
    matrix, pairs = check_draft(db, owner, draft, lock=True)
    if matrix is None:
        matrix = Matrix(owner=owner, name=draft.name, latest=0)
        db.add(matrix)
        flush(db)
    else:
        current = db.get(Version, (matrix.id, matrix.latest))
        previous = cipher.unpack(current.payload)
        if draft.name == previous["name"] and [list(pair) for pair in pairs] == previous["pairs"]:
            return matrix, current
    matrix.name = draft.name
    matrix.latest += 1
    version = Version(matrix_id=matrix.id, number=matrix.latest,
                      payload=cipher.pack({"name": draft.name, "pairs": pairs}))
    db.add(version)
    flush(db)
    return matrix, version


def flush(db):
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "Название матрицы уже занято. Выберите другое название.") from exc


def record(db, cipher, owner, kind, ref, changed, label, operation_id=None):
    event = History(owner=owner, kind=kind, matrix_id=ref.id, version=ref.version,
                    changed=changed, label=cipher.pack(label) if label else None)
    if operation_id:
        event.id = operation_id
    db.add(event)
    db.flush()


def recent_history(db, cipher, owner, limit, offset):
    query = (select(History, Version)
             .join(Version, (Version.matrix_id == History.matrix_id) & (Version.number == History.version))
             .where(History.owner == owner, History.created >= utcnow() - timedelta(days=90))
             .order_by(History.created.desc(), History.id.desc()).limit(limit).offset(offset))
    return [{"id": event.id, "kind": event.kind, "matrix": {"id": event.matrix_id, "version": event.version},
             "name": cipher.unpack(version.payload)["name"], "changed": event.changed,
             "label": cipher.unpack(event.label) if event.label else "", "created": event.created}
            for event, version in db.execute(query)]


def reference(matrix, version):
    return MatrixRef(id=matrix.id, version=version.number)
