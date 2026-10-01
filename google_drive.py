import os

import requests

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class GoogleDriveError(Exception):
    pass


def _get_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise GoogleDriveError("Не задан GOOGLE_API_KEY в переменных окружения (.env)")
    return api_key


def list_children(folder_id: str) -> dict:
    """
    Возвращает содержимое папки: {"folders": [{"id","name"}], "files": [{"id","name"}]}.
    В "files" попадают только .docx и нативные Google Docs (их тоже можно
    скачать как .docx через export) — остальные типы файлов игнорируются.
    """
    api_key = _get_api_key()
    params = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "key": api_key,
        "fields": "files(id,name,mimeType)",
        "pageSize": 1000,
        "orderBy": "folder,name_natural",
    }
    response = requests.get(f"{DRIVE_API_BASE}/files", params=params, timeout=15)
    if response.status_code != 200:
        raise GoogleDriveError(
            f"Google Drive API вернул {response.status_code}: {response.text[:300]}"
        )
    items = response.json().get("files", [])

    folders = [{"id": i["id"], "name": i["name"]} for i in items if i["mimeType"] == FOLDER_MIME]
    files = [
        {"id": i["id"], "name": i["name"], "is_google_doc": i["mimeType"] == GOOGLE_DOC_MIME}
        for i in items
        if i["mimeType"] in (DOCX_MIME, GOOGLE_DOC_MIME) or i["name"].lower().endswith(".docx")
    ]
    return {"folders": folders, "files": files}


def download_docx_bytes(file_id: str, is_google_doc: bool = False) -> bytes:
    """
    Скачивает файл как .docx. Обычный загруженный .docx качается напрямую
    (alt=media). Нативный Google Docs документ нужно экспортировать в
    формат .docx отдельным запросом.
    """
    api_key = _get_api_key()
    if is_google_doc:
        url = f"{DRIVE_API_BASE}/files/{file_id}/export"
        params = {"mimeType": DOCX_MIME, "key": api_key}
    else:
        url = f"{DRIVE_API_BASE}/files/{file_id}"
        params = {"alt": "media", "key": api_key}

    response = requests.get(url, params=params, timeout=30)
    if response.status_code != 200:
        raise GoogleDriveError(
            f"Не удалось скачать файл с Google Drive ({response.status_code}): {response.text[:300]}"
        )
    return response.content
