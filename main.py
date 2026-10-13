import base64
import io
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from docx_parser import ParsedArticle, parse_article, replace_image_placeholders
from google_drive import GoogleDriveError, download_docx_bytes, list_children
from image_gen import ImageGenError, generate_preview
from slug_utils import slugify_ru
from strapi_client import StrapiClient
from video_utils import parse_video_url
from yandex_disk import YandexDiskError, download_image_bytes

load_dotenv()

app = FastAPI(title="Article Parser — Эволюция")

_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_default_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_auth(x_app_password: str = Header(default="")):
    """Общий пароль для /api/*-эндпоинтов, которые трогают контент/публикацию.
    Если APP_PASSWORD не задан — проверка выключена (для локальной разработки)."""
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        return
    if x_app_password != expected:
        raise HTTPException(status_code=401, detail="Неверный пароль доступа")


# Картинки между шагами (парсинг -> генерация превью -> публикация)
# хранятся на диске, а не в памяти процесса: так они переживают
# перезапуск сервера (деплой новой версии, падение, systemd-рестарт)
# и не копятся бесконечно в оперативной памяти. Файл называется
# <token>.<расширение>, само расширение подсказывает mime-тип при чтении.
# Так JSON-ответ остаётся маленьким — вместо гигантской base64-строки
# внутри markdown там просто короткая ссылка вида /api/image/<token>.
IMAGE_STORE_DIR = Path(__file__).parent / "image_store"
IMAGE_STORE_DIR.mkdir(exist_ok=True)


def _store_image(data: bytes, mime: str) -> str:
    token = secrets.token_hex(8)
    ext = mimetypes.guess_extension(mime) or ".bin"
    (IMAGE_STORE_DIR / f"{token}{ext}").write_bytes(data)
    return token


def _load_image(token: str) -> tuple[bytes, str] | None:
    # token — случайный hex, но на всякий случай не даём выйти за пределы папки
    if not re.fullmatch(r"[0-9a-f]+", token):
        return None
    matches = list(IMAGE_STORE_DIR.glob(f"{token}.*"))
    if not matches:
        return None
    path = matches[0]
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path.read_bytes(), mime


def _cleanup_old_images(max_age_hours: float = 48) -> None:
    """Удаляет файлы старше max_age_hours — вызывается при старте сервера,
    чтобы папка не росла бесконечно от незавершённых/забытых генераций."""
    import time
    cutoff = time.time() - max_age_hours * 3600
    for path in IMAGE_STORE_DIR.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


_cleanup_old_images()

REFERENCE_PATH = Path(__file__).parent / "reference_images" / "preview.png"
REFERENCE_PATH.parent.mkdir(exist_ok=True)


@app.get("/api/image/{token}")
def get_image(token: str):
    item = _load_image(token)
    if not item:
        raise HTTPException(status_code=404, detail="Картинка не найдена (или устарела и была удалена)")
    data, mime = item
    return Response(content=data, media_type=mime)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/auth/check", dependencies=[Depends(require_auth)])
def auth_check():
    """Лёгкий эндпоинт только для проверки пароля (форма входа во
    фронтенде реально дёргает именно его, а не просто закрывает окно
    ввода по любому значению): если пароль неверный или не передан
    (а APP_PASSWORD в .env задан), require_auth сам вернёт 401."""
    return {"status": "ok"}


# ============== Шаг 1: источник статей — Google Диск ==============
# Редакция кладёт .docx (или Google Docs) в папку на Google Диске.

@app.get("/api/drive/list", dependencies=[Depends(require_auth)])
def api_drive_list(folder_id: str | None = None):
    """
    Список содержимого папки Google Диска: {"folders": [...], "files": [...]}.
    Без folder_id — берётся корневая папка из GOOGLE_DRIVE_ROOT_FOLDER_ID (.env).
    """
    target_folder = folder_id or os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID")
    if not target_folder:
        raise HTTPException(
            status_code=400,
            detail="Не передан folder_id и не задан GOOGLE_DRIVE_ROOT_FOLDER_ID в .env",
        )
    try:
        return list_children(target_folder)
    except GoogleDriveError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============== Шаг 2: парсинг .docx (с компьютера или с Google Диска) ======

def _process_parsed_article(article: ParsedArticle) -> dict:
    """Общий хвост пайплайна после docx_parser.parse_article: картинки —
    в image_store/, финальная форма ответа (та же самая форма ожидает и
    /api/publish). SEO-поля берутся ровно такими, какими их распознал
    docx_parser из служебного блока в документе — никакой генерации через
    нейросеть здесь нет, если блока нет или он неполный, поля просто
    остаются пустыми и заполняются вручную в интерфейсе.

    article.images может содержать как уже скачанные байты (встроенные
    в .docx картинки), так и ссылки на Яндекс.Диск (img.yandex_url) —
    их нужно докачать здесь же, как это делал article-demo."""
    image_srcs: list[str | None] = []
    image_status: list[dict] = []
    for img in article.images:
        if img.data is not None:
            token = _store_image(img.data, img.mime)
            preview_url = f"/api/image/{token}"
            image_srcs.append(preview_url)
            image_status.append({"url": None, "status": "ok", "preview_url": preview_url})
            continue

        if img.yandex_url:
            try:
                img_bytes = download_image_bytes(img.yandex_url)
                mime = mimetypes.guess_type(img.yandex_url)[0] or "image/jpeg"
                token = _store_image(img_bytes, mime)
                preview_url = f"/api/image/{token}"
                image_srcs.append(preview_url)
                image_status.append({"url": img.yandex_url, "status": "ok", "preview_url": preview_url})
            except (YandexDiskError, Exception) as e:
                image_srcs.append(None)
                image_status.append({"url": img.yandex_url, "status": "error", "detail": str(e)})
            continue

        image_srcs.append(None)
        image_status.append({"url": None, "status": "error", "detail": "Пустая картинка"})

    content_md = replace_image_placeholders(article.content_md, image_srcs)
    first_image_token = None
    for src in image_srcs:
        if src:
            first_image_token = src.rsplit("/", 1)[-1]
            break

    return {
        "title": article.title,
        "content_md": content_md,
        "slug": article.slug,
        "seo_title": article.seo_title,
        "meta_description": article.meta_description,
        "keywords": article.keywords,
        "collection": os.getenv("STRAPI_DEFAULT_COLLECTION", "blogs"),
        "publish_now": False,
        "images_found": len(article.images),
        "image_status": image_status,
        "first_image_token": first_image_token,
    }


@app.post("/api/parse-docx", dependencies=[Depends(require_auth)])
async def api_parse_docx(file: UploadFile = File(...)):
    """Парсинг .docx, загруженного вручную с компьютера."""
    raw = await file.read()
    try:
        return _parse_docx_bytes(raw, known_title="")
    except Exception as e:
        # pandoc может упасть на битом/не-.docx файле — отдаём понятную
        # ошибку в JSON вместо голого 500, фронт покажет её как есть.
        return {"error": f"Не удалось разобрать документ: {e}", "filename": file.filename}


@app.post("/api/drive/parse-docx", dependencies=[Depends(require_auth)])
def api_drive_parse_docx(file_id: str, filename: str = "", is_google_doc: bool = False):
    """Скачивает файл с Google Диска по его id и парсит точно так же, как /api/parse-docx.
    Заголовок берётся из самого документа (known_title не передаём) — как
    и во всём остальном парсере, это восстановленное поведение article-demo."""
    try:
        raw = download_docx_bytes(file_id, is_google_doc=is_google_doc)
    except GoogleDriveError as e:
        raise HTTPException(status_code=500, detail=str(e))
    try:
        return _parse_docx_bytes(raw, known_title="")
    except Exception as e:
        return {"error": f"Не удалось разобрать документ: {e}", "filename": filename}


def _parse_docx_bytes(raw: bytes, known_title: str) -> dict:
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_docx = tmp_dir / "article.docx"
    tmp_docx.write_bytes(raw)
    try:
        article = parse_article(str(tmp_docx), tmp_dir, known_title=known_title)
        return _process_parsed_article(article)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============== Шаг 3: превью-картинка ==============

@app.post("/api/reference-image", dependencies=[Depends(require_auth)])
async def set_reference_image(file: UploadFile = File(...)):
    """Сохраняет образец фирменного стиля превью — подмешивается во все
    последующие генерации, пока не заменишь его новым."""
    data = await file.read()
    REFERENCE_PATH.write_bytes(data)
    return {"status": "ok"}


@app.get("/api/reference-image")
def get_reference_image():
    if not REFERENCE_PATH.exists():
        raise HTTPException(status_code=404, detail="Референс ещё не задан")
    return Response(content=REFERENCE_PATH.read_bytes(), media_type="image/png")


@app.get("/api/reference-image-status")
def reference_image_status():
    return {"is_set": REFERENCE_PATH.exists()}


class GeneratePreviewRequest(BaseModel):
    title: str
    subject: str = ""
    source_token: str | None = None
    source_image_base64: str | None = None


@app.post("/api/generate-preview", dependencies=[Depends(require_auth)])
def api_generate_preview(req: GeneratePreviewRequest):
    source_bytes: bytes | None = None
    if req.source_image_base64:
        try:
            source_bytes = base64.b64decode(req.source_image_base64)
        except Exception:
            raise HTTPException(status_code=400, detail="Некорректный base64 в source_image_base64")
    elif req.source_token:
        item = _load_image(req.source_token)
        if item:
            source_bytes = item[0]

    reference_bytes = REFERENCE_PATH.read_bytes() if REFERENCE_PATH.exists() else None

    try:
        preview_bytes = generate_preview(
            title=req.title, subject=req.subject,
            source_image_bytes=source_bytes, reference_image_bytes=reference_bytes,
        )
    except ImageGenError as e:
        raise HTTPException(status_code=500, detail=str(e))

    token = _store_image(preview_bytes, "image/png")
    return {"preview_token": token, "preview_url": f"/api/image/{token}"}


# ============== Шаг 4: публикация в Strapi ==============

LOCAL_IMAGE_TAG_RE = re.compile(r"!\[(.*?)\]\(/api/image/([a-f0-9]+)\)")


def _upload_local_images_to_strapi(content_md: str, client: StrapiClient, name_prefix: str = "content-image") -> str:
    """Находит все ![](/api/image/<token>) в тексте, грузит соответствующие
    байты (сохранённые на шаге парсинга) в Strapi Media Library и заменяет
    ссылку на постоянный url из Strapi. Файлы называются <name_prefix>-1,
    <name_prefix>-2 ... в порядке появления в тексте."""
    counter = 0

    def _replace(match: re.Match) -> str:
        alt_text, token = match.group(1), match.group(2)
        item = _load_image(token)
        if not item:
            # картинка не найдена на диске (истёк срок хранения, 48ч)
            # — оставляем как есть, чтобы не потерять место
            return match.group(0)
        nonlocal counter
        counter += 1
        data, mime = item
        ext = (mime.split("/")[-1] or "jpg").split("+")[0]  # image/svg+xml -> svg
        url = client.upload_file_url(
            io.BytesIO(data), filename=f"{name_prefix}-{counter}.{ext}", mime=mime
        )
        return f"![{alt_text}]({url})"

    return LOCAL_IMAGE_TAG_RE.sub(_replace, content_md)


VIDEO_FIELDS = ("video", "bottomVideo")  # одинаковые по структуре компоненты в схеме статьи


class PublishRequest(BaseModel):
    title: str
    content_md: str
    slug: str | None = None
    seo_title: str = ""
    meta_description: str = ""
    keywords: list[str] = []
    cover_token: str | None = None
    collection: str = "blogs"
    publish_now: bool = False
    # documentId категории в Strapi (не числовой id — в Strapi 5 связь по
    # короткому REST-формату находится именно по documentId, см. list_categories)
    category_id: str | None = None
    # Дата статьи в формате YYYY-MM-DD. В Strapi у статьи поле date
    # обязательное — без него запись с publish_now=True не проходит
    # валидацию (черновик проходит: Strapi 5 проверяет required-поля
    # только при публикации). Если фронтенд не прислал — ставим сегодня.
    date: str | None = None
    publish_at: str | None = None  # ISO 8601 с часовым поясом — отложенная публикация
    video_url: str | None = None   # ссылка на YouTube / Rutube / Vimeo
    video_title: str = ""          # поле "Название" компонента видео
    video_field: str = "bottomVideo"  # в какое поле Strapi класть видео: video или bottomVideo


def _parse_publish_at(value: str) -> str:
    """Приводит publish_at к UTC ISO ('...Z') и проверяет, что момент в будущем."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="publish_at должен быть датой в формате ISO 8601")
    if moment.tzinfo is None:
        raise HTTPException(status_code=400, detail="publish_at должен содержать часовой пояс")
    moment = moment.astimezone(timezone.utc)
    if moment <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Время отложенной публикации уже прошло")
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@app.get("/api/categories", dependencies=[Depends(require_auth)])
def api_categories():
    """Список категорий блога (collection blog-categories) для выпадающего
    списка на публикации. Если запрос к Strapi упадёт — просто отдаём
    пустой список, чтобы это не блокировало саму публикацию статьи."""
    try:
        client = StrapiClient()
        collection = os.getenv("STRAPI_CATEGORY_COLLECTION", "blog-categories")
        return {"categories": client.list_categories(collection)}
    except Exception as e:
        return {"categories": [], "error": str(e)}


class SlugRequest(BaseModel):
    title: str


@app.post("/api/slug", dependencies=[Depends(require_auth)])
def api_slug(req: SlugRequest):
    """Новый slug из названия, уникальный среди статей в Strapi (черновики тоже учитываются)."""
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Сначала заполните заголовок")
    try:
        return {"slug": StrapiClient().unique_slug(req.title)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/publish", dependencies=[Depends(require_auth)])
def api_publish(req: PublishRequest):
    """
    Публикует статью в Strapi:
      1) находит в content_md ссылки на наши локальные /api/image/<token>,
         грузит соответствующие картинки в Strapi Media Library и
         подставляет вместо них постоянные url
      2) если передан cover_token — грузит эту картинку отдельно и
         подставляет как обложку (поле "img")
      3) создаёт запись в коллекции с полями title/slug/body/metadata
         (metadata.title/description/keywords — см. strapi_client.py),
         category — если передан category_id
      4) если передан publish_at — планирует публикацию черновика через
         плагин Publisher
    """
    execute_at = _parse_publish_at(req.publish_at) if req.publish_at else None
    if execute_at and req.publish_now:
        raise HTTPException(status_code=400, detail="Нельзя одновременно публиковать сразу и отложенно")
    video = None
    if req.video_url and req.video_url.strip():
        if req.video_field not in VIDEO_FIELDS:
            raise HTTPException(status_code=400, detail=f"video_field должен быть одним из: {VIDEO_FIELDS}")
        parsed_video = parse_video_url(req.video_url)
        if not parsed_video:
            raise HTTPException(
                status_code=400,
                detail="Не удалось распознать ссылку на видео. Поддерживаются YouTube, Rutube и Vimeo.",
            )
        if not req.video_title.strip():
            raise HTTPException(status_code=400, detail="Укажите название видео — в Strapi это обязательное поле")
        video = {"title": req.video_title.strip(), "type": parsed_video[0], "value": parsed_video[1]}
    try:
        client = StrapiClient()
        # Папки Media Library недоступны через Content API, поэтому файлы
        # группируем именем: <slug>-cover.png, <slug>-1.jpg ... — их легко найти
        # поиском в "API Uploads".
        file_prefix = (req.slug or slugify_ru(req.title) or "article")[:80].strip("-")
        final_body = _upload_local_images_to_strapi(req.content_md, client, name_prefix=file_prefix)

        cover_id = None
        if req.cover_token:
            item = _load_image(req.cover_token)
            if item:
                data, mime = item
                cover_id = client.upload_file(io.BytesIO(data), filename=f"{file_prefix}-cover.png", mime=mime)

        result = client.create_article(
            title=req.title,
            body_md=final_body,
            slug=req.slug,
            seo_title=req.seo_title,
            meta_description=req.meta_description,
            keywords=req.keywords,
            cover_id=cover_id,
            category_id=req.category_id,
            date=req.date,
            collection=req.collection,
            publish=req.publish_now,
            video=video,
            video_field=req.video_field,
        )
        if execute_at:
            # Черновик уже создан — если планирование не удалось, не отдаём 500
            # (повторная отправка создала бы дубль), а возвращаем ошибку рядом с результатом.
            try:
                client.schedule_publish(result["data"]["documentId"], execute_at)
                result["scheduled_at"] = execute_at
            except Exception as e:
                result["schedule_error"] = str(e)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
