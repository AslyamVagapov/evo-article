"""
Клиент для публикации статьи в Strapi 5.

Схема подобрана по реальному ответу API твоей коллекции "Блог - Статьи"
(api::blog.blog, REST-эндпоинт /api/blogs):

  {
    "title": "...",
    "slug": "...",                  # обязательное поле (UID), уникальное
    "body": "...markdown...",       # тело статьи (НЕ "content"!)
    "metadata": {                   # вложенный компонент, не relation
      "title": "...",               # SEO-title
      "description": "...",         # meta description
      "keywords": "раз, два, три",  # СТРОКА через запятую, не массив!
      "robots": "index, follow"
    }
  }

Если схема у тебя всё же отличается (например REST-имя коллекции не
"blogs", или что-то переименовали) — поправь COLLECTION_DEFAULT и
структуру _build_payload() ниже.
"""
import os
from datetime import date as _date

import requests

from slug_utils import slugify_ru

COLLECTION_DEFAULT = "blogs"  # REST-имя коллекции; проверь запросом
                               # GET {STRAPI_URL}/api/blogs?pagination[limit]=1
                               # если 404 — попробуй "blog" или другое имя
CATEGORY_COLLECTION_DEFAULT = "blog-categories"  # REST-имя коллекции категорий;
                               # проверь так же — если 404, попробуй "blog-category"

DEFAULT_ROBOTS = "index, follow"


def _raise_for_strapi_error(response) -> None:
    """Как response.raise_for_status(), но с настоящим текстом ошибки от
    Strapi. Стандартный вариант пишет только "400 Bad Request for url",
    а причина (какое именно поле не прошло проверку) лежит в теле ответа:
    {"error": {"message": "...", "details": {"errors": [{"path": [...], "message": "..."}]}}}
    """
    if response.ok:
        return
    message = ""
    try:
        error = response.json().get("error", {})
        message = error.get("message", "")
        details = error.get("details")
        errors = details.get("errors") if isinstance(details, dict) else None
        if errors:
            parts = [
                f"{'.'.join(str(p) for p in e.get('path', []))}: {e.get('message', '')}".strip(": ")
                for e in errors
            ]
            message = f"{message} — {'; '.join(parts)}" if message else "; ".join(parts)
    except ValueError:
        message = response.text[:300]
    raise RuntimeError(f"Strapi вернул {response.status_code}: {message or response.reason}")


class StrapiClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.getenv("STRAPI_URL", "")).rstrip("/")
        self.token = token or os.getenv("STRAPI_TOKEN", "")
        if not self.base_url or not self.token:
            raise RuntimeError(
                "Не заданы STRAPI_URL и/или STRAPI_TOKEN в переменных окружения (.env)"
            )

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _upload_raw(self, file_obj, filename: str, mime: str) -> dict:
        """Загружает файл, возвращает полный объект Media Library Strapi (id, url, ...)."""
        files = {"files": (filename, file_obj, mime)}
        response = requests.post(
            f"{self.base_url}/api/upload",
            files=files,
            headers=self._headers(),
            timeout=60,
        )
        _raise_for_strapi_error(response)
        return response.json()[0]

    def upload_file(self, file_obj, filename: str = "image.png", mime: str = "image/png") -> int:
        """Загружает файл, возвращает id — для relation/media-полей (например превью)."""
        return self._upload_raw(file_obj, filename, mime)["id"]

    def upload_file_url(self, file_obj, filename: str = "image.png", mime: str = "image/png") -> str:
        """Загружает файл, возвращает АБСОЛЮТНЫЙ url — для вставки картинки в тело статьи."""
        item = self._upload_raw(file_obj, filename, mime)
        url = item["url"]
        return url if url.startswith("http") else f"{self.base_url}{url}"

    def list_categories(self, collection: str = CATEGORY_COLLECTION_DEFAULT) -> list[dict]:
        """Возвращает список категорий блога (documentId + title) для
        выпадающего списка в интерфейсе. Поддерживает и Strapi v4
        (data[].attributes.title, только числовой id), и v5 (title лежит
        прямо в data[], плюс появляется строковый documentId).

        Важно: в Strapi 5 связь (relation) через короткий формат REST API
        ("category": "...") нужно указывать именно через documentId, а не
        через числовой id — иначе Strapi не находит совпадение и молча
        пропускает привязку без ошибки (публикация статьи проходит успешно,
        но категория остаётся пустой). Поэтому document_id — приоритетное
        значение, а числовой id — только запасной вариант для старых
        версий Strapi, где documentId не существует."""
        response = requests.get(
            f"{self.base_url}/api/{collection}",
            headers=self._headers(),
            params={"pagination[limit]": 200, "sort": "title:asc"},
            timeout=15,
        )
        _raise_for_strapi_error(response)
        items = response.json().get("data", [])
        result = []
        for item in items:
            attrs = item.get("attributes", item)
            document_id = item.get("documentId") or attrs.get("documentId")
            result.append({
                "id": item.get("id"),
                "document_id": document_id or item.get("id"),
                "title": attrs.get("title") or attrs.get("name") or "",
            })
        return result

    def create_article(
        self,
        title: str,
        body_md: str,
        slug: str | None = None,
        seo_title: str = "",
        meta_description: str = "",
        keywords: list[str] | None = None,
        cover_id: int | None = None,
        category_id: str | None = None,  # это document_id категории, не числовой id
        date: str | None = None,          # обязательное поле Strapi "date", формат YYYY-MM-DD
        collection: str = COLLECTION_DEFAULT,
        extra_fields: dict | None = None,
        publish: bool = False,
        video: dict | None = None,        # {"title": ..., "type": "youtube|rutube|vimeo", "value": "<id>"}
        video_field: str = "bottomVideo", # в какое поле Strapi класть видео: "video" или "bottomVideo"
    ) -> dict:
        data = {
            "title": title,
            "slug": slug or slugify_ru(title),
            "body": body_md,
            "metadata": {
                "title": seo_title or title,
                "description": meta_description,
                "keywords": ", ".join(keywords) if keywords else "",
                "robots": DEFAULT_ROBOTS,
                **({"img": cover_id} if cover_id is not None else {}),
            },
        }
        if cover_id is not None:
            data["img"] = cover_id  # поле обложки — верхнего уровня, не внутри metadata
        if category_id is not None:
            data["category"] = category_id
        if video:
            data[video_field] = video  # одиночный компонент (не repeatable) — объект, не список
        # Поле date в Strapi обязательное: черновик сохранится и без него, а вот
        # публикация (в том числе позже, из админки) упадёт с "date must be defined".
        # Поэтому отправляем всегда; если дату не передали — берём сегодняшнюю.
        data["date"] = date or _date.today().isoformat()

        if extra_fields:
            data.update(extra_fields)

        params = {"status": "published" if publish else "draft"}

        response = requests.post(
            f"{self.base_url}/api/{collection}",
            json={"data": data},
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        _raise_for_strapi_error(response)
        return response.json()

    def unique_slug(self, title: str, collection: str = COLLECTION_DEFAULT) -> str:
        """Slug из названия, которого ещё нет в Strapi: при совпадении добавляет
        -2, -3 и т.д. status=draft нужен, чтобы увидеть и неопубликованные
        статьи — в Strapi 5 без него REST отдаёт только опубликованные."""
        base = slugify_ru(title) or "article"
        response = requests.get(
            f"{self.base_url}/api/{collection}",
            headers=self._headers(),
            params={
                "filters[slug][$startsWith]": base,
                "status": "draft",
                "fields[0]": "slug",
                "pagination[pageSize]": 100,
            },
            timeout=15,
        )
        _raise_for_strapi_error(response)
        taken = {
            item.get("slug") or item.get("attributes", {}).get("slug", "")
            for item in response.json().get("data", [])
        }
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def schedule_publish(
        self,
        document_id: str,
        execute_at: str,  # ISO 8601 в UTC, например 2026-10-09T19:00:00.000Z
        entity_slug: str | None = None,
    ) -> dict:
        """Планирует публикацию черновика через плагин Publisher: время
        срабатывания хранит и отслеживает сам Strapi, наш бэкенд после этого
        не нужен. Маршрут публичный (/api/publisher/actions), авторизация —
        обычный API-токен; у токена должны быть права Publisher → create.
        entityId здесь — documentId статьи, а не числовой id."""
        response = requests.post(
            f"{self.base_url}/api/publisher/actions",
            json={
                "data": {
                    "mode": "publish",
                    "entityId": document_id,
                    "entitySlug": entity_slug or os.getenv("STRAPI_ENTITY_SLUG", "api::blog.blog"),
                    "executeAt": execute_at,
                    "locale": None,
                }
            },
            headers=self._headers(),
            timeout=30,
        )
        _raise_for_strapi_error(response)
        return response.json()
