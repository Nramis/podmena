from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from excel import Area, MAX_COLS, MAX_PAIR_LENGTH, MAX_PAIRS, MAX_ROWS
from .documents import MAX_TEXT


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


def printable(value):
    if not value.isprintable():
        raise ValueError("Уберите управляющие символы.")
    return value


Identifier = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
MatrixName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), AfterValidator(printable)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
VersionNumber = Annotated[int, Field(gt=0)]


class Credentials(Input):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+\-]*$")]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class Pair(Input):
    original: Annotated[str, StringConstraints(max_length=MAX_PAIR_LENGTH)]
    substitute: Annotated[str, StringConstraints(max_length=MAX_PAIR_LENGTH)]
    kind: Literal["text", "number"] = "text"


Pairs = Annotated[list[Pair], Field(max_length=MAX_PAIRS)]


def pair_values(pairs):
    return [(p.original, p.substitute, "number") if p.kind == "number" else (p.original, p.substitute)
            for p in pairs]


class MatrixRef(Input):
    id: Identifier
    version: VersionNumber


class MatrixDraft(Input):
    name: MatrixName
    pairs: Pairs
    base: Optional[MatrixRef] = None


class ArchiveInput(Input):
    archived: bool


class ImportInput(Input):
    format: Literal["podmena-matrix-v1"]
    name: MatrixName
    pairs: Pairs
    version: Optional[VersionNumber] = None


class Region(Input):
    sheet: Annotated[int, Field(ge=0)]
    top: Annotated[int, Field(ge=1, le=MAX_ROWS)]
    bottom: Annotated[int, Field(ge=1, le=MAX_ROWS)]
    left: Annotated[int, Field(ge=1, le=MAX_COLS)]
    right: Annotated[int, Field(ge=1, le=MAX_COLS)]

    @model_validator(mode="after")
    def ordered(self):
        if self.top > self.bottom or self.left > self.right:
            raise ValueError("Границы диапазона указаны в обратном порядке.")
        return self

    def area(self):
        return Area(self.sheet, self.top, self.left, self.bottom, self.right)


Regions = Annotated[list[Region], Field(max_length=32)]


class SelectionInput(Input):
    areas: Regions
    pairs: Pairs


class PreviewInput(Input):
    mode: Literal["mask", "restore"]
    areas: Regions
    draft: Optional[MatrixDraft] = None
    matrix: Optional[MatrixRef] = None

    @model_validator(mode="after")
    def check_mode(self):
        if self.mode == "mask" and self.draft is not None and self.matrix is None:
            return self
        if self.mode == "restore" and self.matrix is not None and self.draft is None:
            return self
        raise ValueError("Укажите матрицу для выбранной операции.")


class DownloadInput(Input):
    acknowledged: bool = False
    label: Label = ""


class DocumentText(Input):
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)]


class TextInput(DocumentText):
    matrix: MatrixRef
    label: Label = ""


class MaskTextInput(DocumentText):
    draft: MatrixDraft
    label: Label = ""
    preview: bool = False
