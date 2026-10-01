import os
import requests
from slug_utils import slugify_ru

COLLECTION_DEFAULT = "blogs"  # REST-имя коллекции; проверь запросом
                               # GET {STRAPI_URL}/api/blogs?pagination[limit]=1
                               # если 404 — попробуй "blog" или другое имя

DEFAULT_ROBOTS = "index, follow"


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
        response.raise_for_status()
        return response.json()[0]

    def upload_file(self, file_obj, filename: str = "image.png", mime: str = "image/png") -> int:
        """Загружает файл, возвращает id — для relation/media-полей (например превью)."""
        return self._upload_raw(file_obj, filename, mime)["id"]

    def upload_file_url(self, file_obj, filename: str = "image.png", mime: str = "image/png") -> str:
        """Загружает файл, возвращает АБСОЛЮТНЫЙ url — для вставки картинки в тело статьи."""
        item = self._upload_raw(file_obj, filename, mime)
        url = item["url"]
        return url if url.startswith("http") else f"{self.base_url}{url}"

    def list_categories(self, collection: str = "blog-categories") -> list[dict]:
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
        response.raise_for_status()
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
        collection: str = COLLECTION_DEFAULT,
        extra_fields: dict | None = None,
        publish: bool = False,
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
        response.raise_for_status()
        return response.json()
