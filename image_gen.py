import base64
import io
import os
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageOps

TARGET_SIZE = (1920, 1080)
GENERATION_SIZE = "1536x1024"  # ближайший поддерживаемый размер по пропорциям, докадрируется до TARGET_SIZE

LOGO_PATH = Path(__file__).parent / "logo.png"  # белый леттеринг "ЭВОЛЮЦИЯ" на прозрачном фоне
LOGO_TARGET_WIDTH = 300   # px при холсте 1920 шириной
LOGO_MARGIN = 46          # отступ от края холста, px

DEFAULT_MODEL = "gpt-image-2"

# Единственный стиль превью для Эволюции (в отличие от article-demo, где было
# две категории scene/detail) — диагональная синяя панель слева с заголовком
# и фото товара/сюжета справа, срезанным по той же диагонали. Стиль снят с
# трёх реальных образцов редакции: сплошной фирменный синий (#0086bc,
# такой же, как шапка сайта evosiz.ru), тонкая более светлая голубая линия
# по срезу, крупный белый заголовок слева.
PROMPT_TEMPLATE = (
    "YouTube-превью для статьи интернет-магазина спецодежды и средств "
    "индивидуальной защиты (СИЗ), 16:9, фотореалистично. "
    "Композиция разделена по диагонали (угол наклона среза 18-22 градусов "
    "от вертикали, сверху левее — снизу правее): левая часть кадра "
    "(примерно 55-60% ширины) — это сплошная закрашенная панель фирменного "
    "насыщенного сине-голубого цвета (похож на #0086bc), без градиентов и "
    "текстур, вдоль самой диагональной границы — тонкая (3-5 px в масштабе "
    "кадра) линия чуть более светлого голубого/бирюзового цвета. "
    "Правая часть кадра (за диагональю) — фотореалистичное изображение по "
    "теме статьи: {subject}, резкое, с хорошим освещением, кадр немного "
    "крупным планом, фон нейтральный или соответствующий рабочей обстановке "
    "(склад, мастерская, производство, стройка — по смыслу темы), фото "
    "визуально \"заходит\" в кадр из-под диагонального среза, без рамок."
    "{title_block}"
    " В верхнем левом углу синей панели оставь свободное, визуально спокойное "
    "пространство без текста и без собственных логотипов размером примерно "
    "15% ширины и 12% высоты кадра — туда отдельно наложится фирменный "
    "логотип. Никаких других надписей, вотермарок или логотипов на "
    "картинке быть не должно."
)

# Двухъярусный заголовок, когда SEO-title содержит двоеточие (например
# "Руки под защитой: всё о защитных перчатках") — основная часть крупным
# жирным шрифтом, продолжение чуть меньшим и не таким жирным, оба белые,
# в левой части синей панели, без подложек/табличек — просто текст на
# сплошном синем фоне, как в образцах.
TITLE_BLOCK_TWO_TIER = (
    " На синей панели, в левой части, чуть ниже свободного места под "
    "логотип — заголовок в две строчные группы, обе белым цветом, шрифт "
    "крупный жирный без засечек, текст выровнен по левому краю: первая, "
    "более крупная и жирная — «{title_main}»; сразу под ней, чуть меньшим "
    "и менее жирным начертанием того же шрифта — «{title_sub}»."
    "{fill_hint}"
)
TITLE_BLOCK_SINGLE_TIER = (
    " На синей панели, в левой части, чуть ниже свободного места под "
    "логотип — заголовок белым цветом, крупный жирный шрифт без засечек, "
    "выровнен по левому краю, при необходимости в 2-3 строки: «{title_main}»."
    "{fill_hint}"
)

# Модель генерации то оставляет большой пустой отступ снизу панели (текст
# короткий, кегль маленький), то наоборот — при слишком крупном кегле текст
# уходит вниз и обрезается краем кадра. Первое требование строго важнее
# второго: сначала гарантируем, что ВЕСЬ текст целиком помещается внутри
# панели и виден, и только при этом условии — заполняем кеглем свободное
# место, а не наоборот.
FILL_HINT = (
    " Главное условие по заголовку — он должен ПОЛНОСТЬЮ помещаться внутри "
    "синей панели: ни одна буква, ни одна строка не должна обрезаться, "
    "выходить за нижний, левый или диагональный край панели или наезжать на "
    "правую фотографическую часть кадра. Сначала прикинь, сколько строк "
    "получится с учётом длины текста и переносов, и только исходя из этого "
    "выбирай кегль шрифта — если строк много или текст длинный, сделай "
    "кегль заметно меньше, чтобы всё гарантированно уместилось с отступом "
    "снизу от края панели; если текст короткий — можно увеличить кегль, но "
    "не настолько, чтобы текст полез вниз за пределы панели. Между "
    "заполнением пространства и полной видимостью текста — видимость "
    "важнее: лучше умеренный отступ снизу, чем обрезанный текст."
)


class ImageGenError(Exception):
    pass


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ImageGenError("Не задан OPENAI_API_KEY в переменных окружения (.env)")
    base_url = os.getenv("OPENAI_BASE_URL")
    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))
    kwargs = {"api_key": api_key, "timeout": timeout_seconds}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _get_model() -> str:
    return os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_MODEL)


def _split_title(title: str) -> tuple[str, str]:
    """SEO-title часто уже написан в формате "Тема: уточнение" — делим по
    первому двоеточию на два яруса заголовка. Без двоеточия — один ярус."""
    if ":" in title:
        main, sub = title.split(":", 1)
        return main.strip() + ":", sub.strip()
    return title.strip(), ""


def build_prompt(title: str, subject: str = "", has_reference: bool = False, has_source: bool = False) -> str:
    subject = subject or "товар или сюжет по теме статьи"
    title_main, title_sub = _split_title(title)
    title_block = (
        TITLE_BLOCK_TWO_TIER.format(title_main=title_main, title_sub=title_sub, fill_hint=FILL_HINT)
        if title_sub
        else TITLE_BLOCK_SINGLE_TIER.format(title_main=title_main, fill_hint=FILL_HINT)
    )
    prompt = PROMPT_TEMPLATE.format(subject=subject, title_block=title_block)

    if has_reference and has_source:
        prompt += (
            " Первое приложенное изображение — образец фирменного стиля превью: "
            "повтори точно такую же диагональ, цвет панели и расположение текста. "
            "Второе приложенное изображение — реальный объект, который нужно "
            "показать в фотографической части кадра вместо объекта с образца."
        )
    elif has_reference:
        prompt += (
            " Приложенное изображение — образец фирменного стиля превью: "
            "повтори точно такую же диагональ, цвет панели и расположение текста, "
            "но с другим объектом в кадре согласно описанию выше."
        )
    return prompt


def _fit_to_target(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    fitted = ImageOps.fit(img, TARGET_SIZE, method=Image.LANCZOS)
    out = io.BytesIO()
    fitted.save(out, format="PNG")
    return out.getvalue()


def _composite_logo(image_bytes: bytes) -> bytes:
    """Накладывает настоящий файл логотипа (белый леттеринг на прозрачном
    фоне) поверх сгенерированной картинки в левом верхнем углу. Логотип НЕ
    рисуется нейросетью — так он гарантированно одинаковый на каждом
    превью, без расхождений в шрифте, которые неизбежны при генерации
    текста нейросетью."""
    if not LOGO_PATH.exists():
        return image_bytes

    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(LOGO_PATH).convert("RGBA")

    scale = LOGO_TARGET_WIDTH / logo.width
    logo = logo.resize((LOGO_TARGET_WIDTH, round(logo.height * scale)), Image.LANCZOS)

    x = LOGO_MARGIN
    y = LOGO_MARGIN
    base.paste(logo, (x, y), mask=logo)

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def generate_preview(
    title: str,
    subject: str = "",
    source_image_bytes: bytes | None = None,
    reference_image_bytes: bytes | None = None,
) -> bytes:
    """
    Генерирует превью 1920x1080 в фирменном стиле Эволюции.

    :param source_image_bytes: реальное фото объекта статьи (первое фото
        из статьи или загруженное вручную).
    :param reference_image_bytes: заранее сохранённый образец фирменного
        стиля превью (расположение панели, шрифт) — передаётся модели как
        пример оформления, а не как объект показа.
    """
    client = _get_client()
    model = _get_model()
    prompt = build_prompt(
        title, subject,
        has_reference=reference_image_bytes is not None,
        has_source=source_image_bytes is not None,
    )

    images_to_send = []
    if reference_image_bytes is not None:
        ref_file = io.BytesIO(reference_image_bytes)
        ref_file.name = "reference.png"
        images_to_send.append(ref_file)
    if source_image_bytes is not None:
        src_file = io.BytesIO(source_image_bytes)
        src_file.name = "source.png"
        images_to_send.append(src_file)

    try:
        if images_to_send:
            image_param = images_to_send[0] if len(images_to_send) == 1 else images_to_send
            result = client.images.edit(
                model=model, image=image_param, prompt=prompt, size=GENERATION_SIZE,
            )
        else:
            result = client.images.generate(
                model=model, prompt=prompt, size=GENERATION_SIZE,
            )
    except Exception as e:
        raise ImageGenError(f"Ошибка генерации через OpenAI: {e}") from e

    raw_bytes = base64.b64decode(result.data[0].b64_json)
    fitted = _fit_to_target(raw_bytes)
    return _composite_logo(fitted)
