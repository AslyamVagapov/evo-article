"""Транслитерация кириллицы в slug (kebab-case), общая для docx_parser и strapi_client."""
import re

_TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_ru(text: str) -> str:
    """Транслитерация + приведение к kebab-case. Не гарантирует уникальность —
    при коллизии в Strapi вернётся ошибка валидации unique, тогда slug
    нужно будет поправить вручную перед повторной публикацией."""
    text = text.lower()
    text = "".join(_TRANSLIT_MAP.get(ch, ch) for ch in text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:180]
