import os
from uuid import uuid4

from werkzeug.utils import secure_filename


def is_allowed_file(filename, allowed_extensions):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in allowed_extensions


def _has_expected_signature(file_storage, ext):
    ext = (ext or "").lower()
    # Keep current pointer to avoid side effects for Flask file handlers.
    stream = file_storage.stream
    pos = stream.tell()
    try:
        stream.seek(0)
        head = stream.read(64)
    finally:
        stream.seek(pos)

    # Binary signature checks only for types we rely on most.
    if ext == "pdf":
        return head.startswith(b"%PDF-")
    if ext in {"jpg", "jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"

    # Keep compatibility for doc/docx and other currently allowed types.
    return True


def save_uploaded_file(file_storage, upload_dir, allowed_extensions):
    if not file_storage or file_storage.filename == "":
        return None

    if not is_allowed_file(file_storage.filename, allowed_extensions):
        raise ValueError("Type de fichier non autorise.")

    original = secure_filename(file_storage.filename)
    if not original:
        raise ValueError("Nom de fichier invalide.")

    ext = original.rsplit(".", 1)[1].lower()
    if not _has_expected_signature(file_storage, ext):
        raise ValueError("Le contenu du fichier ne correspond pas a son extension.")

    filename = f"{uuid4().hex}.{ext}"
    os.makedirs(upload_dir, exist_ok=True)

    upload_dir_abs = os.path.abspath(upload_dir)
    path = os.path.abspath(os.path.join(upload_dir_abs, filename))
    if not path.startswith(upload_dir_abs + os.sep):
        raise ValueError("Chemin de destination invalide.")

    file_storage.save(path)
    return filename
