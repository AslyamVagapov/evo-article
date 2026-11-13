import base64
import io
import os
from pathlib import Path

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageOps

TARGET_SIZE = (1920, 1080)
GENERATION_SIZE = "1536x1024"  # ближайший поддерживаемый размер по пропорциям, докадрируется до TARGET_SIZE

LOGO_PATH = Path(__file__).parent / "logo.png"  # белый леттеринг "ЭВОЛЮЦИЯ" на прозрачном фоне
LOGO_TARGET_WIDTH = 300   # px при холсте 1920 шириной
LOGO_MARGIN = 46          # отступ от края холста, px

# Заголовок рисуется кодом уже на готовом холсте 1920x1080. Генеративная
# модель хорошо создаёт фон и фотографию, но не гарантирует, что длинный
# русский текст будет дописан без ошибок и обрезки.
TITLE_LEFT = 70
TITLE_RIGHT = 900  # безопаснее диагональной границы даже в нижней части
TITLE_TOP = 285
TITLE_BOTTOM = 930
TITLE_COLOR = (255, 255, 255, 255)

FONT_BOLD_CANDIDATES = (
    # Docker (пакет fonts-dejavu-core)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    # Windows, локальная разработка
    "C:/Windows/Fonts/arialbd.ttf",
)
FONT_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

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
    "визуально \"заходит\" в кадр из-под диагонального среза, без рамок. "
    "Синюю панель оставь полностью пустой: заголовок будет наложен кодом "
    "после генерации. Не рисуй на ней буквы, слова, цифры или имитацию текста."
    " В верхнем левом углу синей панели оставь свободное, визуально спокойное "
    "пространство без текста и без собственных логотипов размером примерно "
    "15% ширины и 12% высоты кадра — туда отдельно наложится фирменный "
    "логотип. Никаких других надписей, вотермарок или логотипов на "
    "картинке быть не должно. Вообще не добавляй никакой текст ни в одну "
    "часть изображения."
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
    # Заголовок передаётся только как смысловая подсказка для выбора сюжета.
    # Сам текст модель не рисует — это детерминированно делает Pillow.
    subject = subject or title or "товар или сюжет по теме статьи"
    prompt = PROMPT_TEMPLATE.format(subject=subject)

    if has_reference and has_source:
        prompt += (
            " Первое приложенное изображение — образец фирменного стиля превью: "
            "повтори такую же диагональ, цвет панели и композицию, но полностью "
            "игнорируй и не копируй текст и логотип с образца. "
            "Второе приложенное изображение — реальный объект, который нужно "
            "показать в фотографической части кадра вместо объекта с образца."
        )
    elif has_reference:
        prompt += (
            " Приложенное изображение — образец фирменного стиля превью: "
            "повтори такую же диагональ, цвет панели и композицию, полностью "
            "игнорируя текст и логотип с образца, "
            "но с другим объектом в кадре согласно описанию выше."
        )
    return prompt


def _fit_to_target(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    fitted = ImageOps.fit(img, TARGET_SIZE, method=Image.LANCZOS)
    out = io.BytesIO()
    fitted.save(out, format="PNG")
    return out.getvalue()


def _load_title_font(size: int, *, bold: bool) -> ImageFont.FreeTypeFont:
    env_name = "PREVIEW_FONT_BOLD" if bold else "PREVIEW_FONT_REGULAR"
    candidates = (os.getenv(env_name, ""),) + (
        FONT_BOLD_CANDIDATES if bold else FONT_REGULAR_CANDIDATES
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise ImageGenError(
        "Не найден шрифт для заголовка превью. Установите fonts-dejavu-core "
        f"или задайте путь в {env_name}."
    )


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _split_long_word(draw: ImageDraw.ImageDraw, word: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Страховка для обозначений без пробелов, которые шире всей панели."""
    parts: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            parts.append(current)
            current = char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words: list[str] = []
    for word in text.split():
        if _text_width(draw, word, font) > max_width:
            words.extend(_split_long_word(draw, word, font, max_width))
        else:
            words.append(word)

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _line_height(font: ImageFont.FreeTypeFont, text: str) -> int:
    """Общая кириллическая высота: строки имеют одинаковый шаг базовых линий."""
    _, top, _, bottom = font.getbbox("АЙрущ", anchor="ls")
    return bottom - top


def _draw_title_line(draw, x, y, text, font):
    # Одинаковая базовая линия независимо от наличия й, д, у в строке.
    _, top, _, _ = font.getbbox("АЙрущ", anchor="ls")
    left, _, _, _ = font.getbbox(text, anchor="ls")
    draw.text((x - left, y - top), text, font=font, fill=TITLE_COLOR, anchor="ls")


def _composite_title(image_bytes: bytes, title: str) -> bytes:
    """Накладывает полный заголовок без участия генеративной модели.

    Размер шрифта уменьшается до тех пор, пока все строки не войдут в
    безопасную прямоугольную область слева от диагональной границы.
    """
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    main, sub = _split_title(title)
    max_width = TITLE_RIGHT - TITLE_LEFT
    max_height = TITLE_BOTTOM - TITLE_TOP

    layout = None
    # Одну строку предпочитаем только при читаемом кегле. Длинную первую
    # часть переносим по словам, прежде чем уменьшать её до мелкого текста.
    layout_passes = ((1, 76), (5, 32)) if sub else ((6, 32),)
    for max_main_lines, min_main_size in layout_passes:
        for main_size in range(144, min_main_size - 1, -2):
            sub_size = 120
            main_font = _load_title_font(main_size, bold=True)
            if any(_text_width(draw, word, main_font) > max_width for word in main.split()):
                continue
            main_lines = _wrap_text(draw, main, main_font, max_width)
            if len(main_lines) > max_main_lines:
                continue
            main_spacing = max(8, round(main_size * 0.16))
            group_gap = max(24, round(main_size * 0.32)) if sub else 0
            main_height = sum(_line_height(main_font, line) for line in main_lines) + (len(main_lines) - 1) * main_spacing
            # Продолжение подбирается независимо от длины номера ГОСТ.
            # Проверяем реальные размеры строк и занимаем доступную высоту.
            # В многострочном заголовке продолжение не должно подавлять
            # основную тему (например огромным одиночным «требования»).
            max_sub_size = min(120, main_size) if len(main_lines) > 1 else 120
            for sub_size in range(max_sub_size, 23, -2):
                sub_font = _load_title_font(sub_size, bold=False)
                # Не разрываем длинное слово ради крупного кегля.
                if any(_text_width(draw, word, sub_font) > max_width for word in sub.split()):
                    continue
                sub_lines = _wrap_text(draw, sub, sub_font, max_width) if sub else []
                sub_spacing = max(4, round(sub_size * 0.05))
                sub_height = sum(_line_height(sub_font, line) for line in sub_lines) + max(0, len(sub_lines) - 1) * sub_spacing
                if main_height + group_gap + sub_height <= max_height:
                    break
            else:
                continue
            total_height = main_height + group_gap + sub_height
            if total_height <= max_height:
                layout = (
                    main_font, sub_font, main_lines, sub_lines,
                    main_spacing, sub_spacing, group_gap, total_height,
                )
                break
        if layout is not None:
            break

    if layout is None:
        raise ImageGenError("Заголовок слишком длинный для области превью")

    (
        main_font, sub_font, main_lines, sub_lines,
        main_spacing, sub_spacing, group_gap, total_height,
    ) = layout
    y = TITLE_TOP + max(0, (max_height - total_height) // 2)

    for line in main_lines:
        _draw_title_line(draw, TITLE_LEFT, y, line, main_font)
        y += _line_height(main_font, line) + main_spacing
    if main_lines:
        y -= main_spacing
    y += group_gap
    for line in sub_lines:
        _draw_title_line(draw, TITLE_LEFT, y, line, sub_font)
        y += _line_height(sub_font, line) + sub_spacing

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG")
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
    with_title = _composite_title(fitted, title)
    return _composite_logo(with_title)
