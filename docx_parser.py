import mimetypes
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pypandoc

from slug_utils import slugify_ru

# --- Нормализация различий между версиями pandoc (см. article-demo, тот
# же источник расхождений) ---
SETEXT_H1_RE = re.compile(r"^(?!#)(\S[^\n]*?)[ \t]*\n=[=]{2,}[ \t]*$", re.MULTILINE)
SETEXT_H2_RE = re.compile(r"^(?!#)(\S[^\n]*?)[ \t]*\n-[-]{2,}[ \t]*$", re.MULTILINE)
TRAILING_HARD_BREAK_RE = re.compile(r"\\[ \t]*$", re.MULTILINE)

# Часть документов оформляет служебный блок Word-стилем "Цитата" — pandoc
# превращает такой абзац в markdown-blockquote ("> текст", разделители
# между полями — пустая строка "> "). Если это не убрать до остального
# разбора, символы ">" просачиваются в значения полей (например
# "...устройство > >" вместо "...устройство"), потому что стоп-паттерн
# _STOP_AT_NEXT_LABEL ищет обычный перевод строки/пустую строку, а не
# "> " перед следующим полем. Убираем маркер цитаты у каждой строки —
# дальше блок обрабатывается как обычные абзацы, независимо от того,
# каким стилем он был оформлен в Word.
BLOCKQUOTE_MARKER_RE = re.compile(r"^>+[ \t]?", re.MULTILINE)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Убирает HTML-теги (<u>, <span>...), которые pandoc иногда оставляет
    от форматирования Word (подчёркивание и т.п.), сохраняя текст внутри."""
    return _HTML_TAG_RE.sub("", text)


def _normalize_raw_markdown(raw: str) -> str:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = SETEXT_H1_RE.sub(lambda m: f"# {m.group(1).strip()}", raw)
    raw = SETEXT_H2_RE.sub(lambda m: f"## {m.group(1).strip()}", raw)
    raw = TRAILING_HARD_BREAK_RE.sub("", raw)
    raw = BLOCKQUOTE_MARKER_RE.sub("", raw)
    raw = _fix_empty_table_headers(raw)
    return raw


# GFM-синтаксис markdown-таблицы требует строку заголовка. Если у
# Word-таблицы для первой строки НЕ включена опция "Строка заголовка"
# (Table Design → Header Row — это именно опция стиля, а не наличие
# текста в строке), pandoc всё равно обязан вставить заголовок по
# синтаксису и подставляет пустую ("|     |     |"), а строка, которая
# визуально и была заголовком в Word, остаётся обычной строкой данных.
# На сайте такая пустая шапка видна как тонкая пустая полоска над
# таблицей. Чаще всего это случается в табличках-карточках ("Карточка
# связей" и т.п.), где автор просто не проставил этот флажок в Word —
# подтверждено прямым экспериментом (сборка .docx с tblLook
# firstRow="0" на таблице и прогон через pandoc). Лечим так же, как
# поступил бы редактор вручную: убираем пустую строку-шапку, а первая
# строка данных (она почти всегда и задумывалась как заголовок)
# становится настоящим заголовком таблицы.
_MD_TABLE_BLOCK_RE = re.compile(
    r"^\|.*\|[ \t]*\n"
    r"^\|(?:[ \t:-]+\|)+[ \t]*\n"
    r"(?:^\|.*\|[ \t]*\n?)*",
    re.MULTILINE,
)


def _is_blank_table_row(line: str) -> bool:
    cells = line.strip().strip("|").split("|")
    return all(not c.strip() for c in cells)


def _fix_empty_table_headers(content: str) -> str:
    def _replace(m: re.Match) -> str:
        block = m.group(0)
        lines = block.rstrip("\n").split("\n")
        if len(lines) < 3:
            return block
        header, sep, *data_rows = lines
        if not _is_blank_table_row(header) or not data_rows:
            return block
        new_header = data_rows[0]
        rest = data_rows[1:]
        return "\n".join([new_header, sep, *rest]) + "\n"

    return _MD_TABLE_BLOCK_RE.sub(_replace, content)


# --- Служебный SEO-блок ---------------------------------------------------
# Редакция по договорённости снова пишет статьи со служебным блоком
# метаданных в начале документа — как в article-demo (без него SEO-поля
# приходилось бы либо оставлять пустыми, либо генерировать нейросетью, а
# от последнего решили отказаться). Код ниже — прямой перенос парсинга
# из article-demo/docx_parser.py: понимает три равноправных формата,
# встречавшихся на практике, так что не зависит от того, кто и как
# именно оформил конкретный документ.
#   1) "**SEO-title (58 симв.):** значение" / "**Meta description:** значение"
#   2) Сплошной текст без выделения — используется список известных меток
#      как "стоп-слов", чтобы понять, где заканчивается значение
#   3) Markdown-таблица "| **title** | значение |" / "| **description** | значение |"
# Если блока всё же нет (например для статей, парсенных с компьютера без
# него) — поля просто остаются пустыми, seo_title подставляется из
# заголовка статьи.
_KNOWN_LABELS = [
    "SEO-title", "Meta description", "Slug", "Анкор", "Приём", "Проверка",
    "Родительский запрос", "Дочерние запросы", "Видео", "Источники",
    "№ в реестре", "Тип статьи", "Использовано фактов", "Карта источников",
    "Не хватает в базе", "Конфликты и оговорки", "Проверка на каннибализацию",
    "Соседние статьи", "FAQ", "ключи", "Ключевые слова",
    "Также по этой теме", "Похожие статьи", "Теги",
]
_NEXT_LABEL_ALT = "|".join(re.escape(label) for label in _KNOWN_LABELS)
_STOP_AT_NEXT_LABEL = rf"(?=\s+\*{{0,2}}(?:{_NEXT_LABEL_ALT})[^:\n]{{0,40}}:|\n\s*\n|\Z)"

SEO_TITLE_RE = re.compile(
    rf"\*{{0,2}}SEO-title[^:]*:\*{{0,2}}\s*(.*?){_STOP_AT_NEXT_LABEL}",
    re.IGNORECASE | re.DOTALL,
)
META_DESCRIPTION_RE = re.compile(
    rf"\*{{0,2}}Meta description[^:]*:\*{{0,2}}\s*(.*?){_STOP_AT_NEXT_LABEL}",
    re.IGNORECASE | re.DOTALL,
)

# Формат "Метаданные страницы" — таблица вида:
#   | **title**       | значение |
#   | **description** | значение |
#   | **keywords**    | значение |
TABLE_SEO_TITLE_RE = re.compile(r"^\|\s*\*\*title\*\*\s*\|(.*)\|\s*$", re.MULTILINE | re.IGNORECASE)
TABLE_META_DESCRIPTION_RE = re.compile(r"^\|\s*\*\*description\*\*\s*\|(.*)\|\s*$", re.MULTILINE | re.IGNORECASE)
TABLE_KEYWORDS_RE = re.compile(r"^\|\s*\*\*keywords\*\*\s*\|(.*)\|\s*$", re.MULTILINE | re.IGNORECASE)


def _table_row_re(label: str) -> re.Pattern:
    """Строка markdown-таблицы вида "| **Метка** | значение |" — с любым
    количеством жирного выделения вокруг метки (в реальных документах
    встречается и "**Метка**", и просто "Метка")."""
    return re.compile(
        rf"^\|\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}\s*\|(.*)\|\s*$",
        re.MULTILINE | re.IGNORECASE,
    )


# Формат "SEO-паспорт статьи" — отдельная двухколоночная таблица
# ("**Поле** | **Требование / значение**"), встречающаяся как альтернатива
# и формату-абзацу (SEO_TITLE_RE и т.п.), и формату "Метаданные страницы"
# (TABLE_SEO_TITLE_RE и т.п.) выше. Отличается набором меток (английское
# "SEO-title"/"Meta description" остаются, но заголовок статьи и ключевые
# слова называются по-другому: "H1", "Slug", "Главный запрос",
# "Дополнительные запросы") — поэтому заводим под неё отдельные паттерны,
# а не пытаемся впихнуть в уже существующие.
TABLE_H1_RE = _table_row_re("H1")
TABLE_SLUG_RE = _table_row_re("Slug")
TABLE_SEO_TITLE_PASSPORT_RE = _table_row_re("SEO-title")
TABLE_META_DESCRIPTION_PASSPORT_RE = _table_row_re("Meta description")
TABLE_MAIN_QUERY_RE = _table_row_re("Главный запрос")
TABLE_EXTRA_QUERIES_RE = _table_row_re("Дополнительные запросы")

# Блок строк markdown pipe-таблицы, идущих подряд (без пустых строк между
# ними) — используется, чтобы найти ГРАНИЦЫ целой таблицы "SEO-паспорт
# статьи" (а не только отдельной строки с нужным полем), потому что всё,
# что идёт ПОСЛЕ этой таблицы в документе — уже настоящее тело статьи
# (см. _find_passport_table).
_PIPE_TABLE_BLOCK_RE = re.compile(r"(?:^\|.*\|[ \t]*\n?)+", re.MULTILINE)


def _find_passport_table(raw: str) -> re.Match | None:
    """Находит в документе таблицу "SEO-паспорт статьи" — отличаем её от
    остальных табличных форматов служебного блока по наличию строки с
    меткой "H1" (канонический заголовок статьи), которой нет ни в формате
    "Метаданные страницы", ни в обычном абзацном формате."""
    for m in _PIPE_TABLE_BLOCK_RE.finditer(raw):
        if TABLE_H1_RE.search(m.group(0)):
            return m
    return None


# Два формата ключевых слов, встречающихся в реальных документах:
#   1) "**ключи:**" — список на СЛЕДУЮЩЕЙ строке
#   2) "**Ключевые слова:**" — список на ТОЙ ЖЕ строке
KEYWORDS_PATTERNS = [
    re.compile(r"\*\*ключи:\*\*\s*\n+([^\n]+)", re.IGNORECASE),
    re.compile(r"\*\*Ключевые слова:\*\*\s*(.+)", re.IGNORECASE),
    TABLE_KEYWORDS_RE,
]


_PLACEHOLDER_VALUE_RE = re.compile(r"^[-–—]+$")

# В таблице "SEO-паспорт статьи" редакция часто дописывает в конце
# значения SEO-title/Meta description длину в символах для контроля лимита
# — например "...готовые шаблоны. (140)". Это заметка для автора, не часть
# самого текста, и на сайте быть не должна.
_CHAR_COUNT_SUFFIX_RE = re.compile(r"\s*\(\d{1,4}\)\s*$")


def _strip_char_count_suffix(value: str) -> str:
    return _CHAR_COUNT_SUFFIX_RE.sub("", value).strip()

# Если заголовок/поле в Word оформлены гиперссылкой (например, название
# статьи ведёт на карточку товара на сайте), pandoc превращает это в
# markdown-ссылку "[текст](url)" — а не в обычный текст. Для заголовка,
# SEO-title и т.п. нужен только видимый текст ссылки, сам url — не часть
# значения поля.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _strip_markdown_link_syntax(text: str) -> str:
    return _MD_LINK_RE.sub(lambda m: m.group(1), text)


def _extract_field(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    value = _strip_html(m.group(1))
    value = _strip_markdown_link_syntax(value)
    # убираем markdown-выделение (**жирный**), которое иногда остаётся
    # прямо в значении поля из-за форматирования ячейки/строки в Word
    value = value.replace("**", "")
    value = re.sub(r"\s+", " ", value).strip(" *")
    # пустая ячейка с прочерком-заглушкой ("-", "–", "—") — это не значение
    if _PLACEHOLDER_VALUE_RE.match(value):
        return ""
    return value


def _split_keyword_values(raw_value: str) -> list[str]:
    """Делит строку значения на отдельные ключевые слова. Разделитель
    варьируется между документами — встречается и запятая, и точка с
    запятой (например в "Дополнительные запросы" формата "SEO-паспорт
    статьи") — поэтому используем ";" только если он вообще есть в
    значении, иначе запятую как раньше."""
    separator = ";" if ";" in raw_value else ","
    kws = [kw.strip(" *") for kw in raw_value.split(separator) if kw.strip(" *")]
    return [kw for kw in kws if not _PLACEHOLDER_VALUE_RE.match(kw)]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _extract_keywords(full_text: str) -> list[str]:
    for pattern in KEYWORDS_PATTERNS:
        m = pattern.search(full_text)
        if m:
            raw = _strip_html(m.group(1)).replace("**", "")
            kws = _split_keyword_values(raw)
            if kws:
                return kws

    # Формат "SEO-паспорт статьи" не даёт единого списка ключевых слов —
    # вместо этого два отдельных поля таблицы: "Главный запрос" (обычно
    # одна фраза) и "Дополнительные запросы" (список через точку с
    # запятой). Объединяем оба в общий список.
    kws: list[str] = []
    main_query = _extract_field(TABLE_MAIN_QUERY_RE, full_text)
    if main_query:
        kws.append(main_query)
    extra_match = TABLE_EXTRA_QUERIES_RE.search(full_text)
    if extra_match:
        raw_extra = _strip_html(extra_match.group(1)).replace("**", "")
        kws.extend(_split_keyword_values(raw_extra))
    return _dedupe_keep_order(kws)


# --- Заголовок статьи --------------------------------------------------
# В статьях Эволюции нет стиля "Заголовок 1" — название статьи это просто
# первый жирный абзац в начале документа ("**Руки под защитой: ...**").
# Поэтому вместо обязательного "# " (как в article-demo) распознаём:
#   1) настоящий "# Заголовок" (ATX) — если он всё же есть;
#   2) первый непустой абзац, целиком выделенный жирным;
#   3) иначе — просто первый непустой абзац как есть.
#
# Важно: всё, что стоит ДО найденного заголовка — отбрасывается вместе с
# самой строкой заголовка, а не просто "вырезается из середины" — точно
# так же, как в article-demo, где текст статьи брался от найденного H1 и
# дальше (raw[h1_match.start():]). Служебный блок с SEO-метаданными
# редакция пишет как раз перед заголовком, и если его не отбрасывать
# целиком, а только вырезать строку заголовка, он остаётся в теле статьи
# и утекает в превью/публикацию.
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
BOLD_ONLY_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")

# Если в документе нет H1 (Word-стиль "Заголовок 1" не применён к
# названию статьи) — заголовок ищем как первый абзац, который НЕ похож на
# часть служебного блока: не строка-маркер "[СЛУЖЕБНОЕ — НЕ ПУБЛИКОВАТЬ]",
# не поле из списка известных меток ("**SEO-title:** ...", "**Slug:** ...")
# и не строка markdown-таблицы. Без этой проверки первым "заголовком"
# может по ошибке стать сама служебная пометка или одно из её полей —
# они тоже часто оформлены жирным, как и настоящее название статьи.
# Квадратные скобки pandoc при конвертации .docx → markdown экранирует
# обратным слэшем (это просто обычный текст, а не markdown-ссылка) —
# "\[СЛУЖЕБНОЕ...\]" — поэтому оба варианта, с экранированием и без,
# должны опознаваться одинаково.
_SERVICE_MARKER_RE = re.compile(r"^\\?\[[^\]]*(?:СЛУЖЕБН|НЕ\s+ПУБЛИК)[^\]]*\\?\]\s*$", re.IGNORECASE)


def _is_service_block_paragraph(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return True
    if _SERVICE_MARKER_RE.match(stripped):
        return True
    if stripped.startswith("|"):
        return True
    if re.match(rf"^\*{{0,2}}(?:{_NEXT_LABEL_ALT})[^:\n]{{0,40}}:", stripped, re.IGNORECASE):
        return True
    return False


def _clean_title_text(text: str) -> str:
    """Заголовок — это видимый текст, без markdown-разметки: если Word-заголовок
    оформлен гиперссылкой (например, ведёт на карточку товара на сайте),
    pandoc даёт "[текст](url)" — url тут не часть заголовка. Заодно убираем
    жирное выделение, которое иногда применено ко всему заголовку целиком."""
    text = _strip_markdown_link_syntax(text)
    return text.replace("**", "").strip()


def _strip_duplicate_title_paragraphs(rest: str, title: str) -> str:
    """Некоторые документы не используют markdown H1 для заголовка статьи
    вообще — название написано просто жирным абзацем (иногда разбитым на
    два через Shift+Enter в Word), а настоящий заголовок приходит из
    служебной таблицы "SEO-паспорт статьи" (поле H1). В этом случае такой
    жирный абзац(ы) в начале тела — это ДУБЛИКАТ заголовка, который и так
    добавляется в content_md отдельно (см. parse_article), и его нужно
    убрать, иначе заголовок окажется в тексте статьи дважды подряд."""
    normalized_title = re.sub(r"\s+", " ", title).strip(" :").lower()
    if not normalized_title:
        return rest
    blocks = re.split(r"\n\s*\n", rest.strip())
    collected: list[str] = []
    for consumed, block in enumerate(blocks[:2], start=1):
        bold = BOLD_ONLY_LINE_RE.match(block.strip())
        if not bold:
            break
        collected.append(_strip_html(bold.group(1)))
        combined = re.sub(r"\s+", " ", " ".join(collected)).strip(" :").lower()
        if combined == normalized_title:
            return "\n\n".join(blocks[consumed:]).strip()
        if not normalized_title.startswith(combined):
            break
    return rest


def _extract_title(raw: str) -> tuple[str, str]:
    """Возвращает (заголовок, текст статьи после заголовка — без всего,
    что было до него включительно, включая служебный блок)."""
    passport = _find_passport_table(raw)
    if passport:
        passport_title = _extract_field(TABLE_H1_RE, passport.group(0))
        if passport_title:
            rest = _strip_duplicate_title_paragraphs(raw[passport.end():].strip(), passport_title)
            return passport_title, rest

    h1 = H1_RE.search(raw)
    if h1:
        title = _clean_title_text(_strip_html(h1.group(1)))
        rest = raw[h1.end():]
        return title, rest.strip()

    blocks = re.split(r"\n\s*\n", raw.strip())
    for i, block in enumerate(blocks):
        if _is_service_block_paragraph(block):
            continue
        stripped = block.strip()
        bold = BOLD_ONLY_LINE_RE.match(stripped)
        first_line = stripped.splitlines()[0] if stripped else ""
        title = _clean_title_text(_strip_html(bold.group(1) if bold else first_line))
        rest = "\n\n".join(blocks[i + 1:])
        return title, rest.strip()

    return "", raw.strip()


# --- Утечки служебных пометок внутри тела статьи --------------------------
# В части документов редакция вставляет короткую служебную пометку не
# только перед заголовком, но и ЕЩЁ РАЗ — отдельным абзацем сразу после
# вступительного абзаца, уже внутри самого текста статьи, например:
#   "**Модель:** К-701 и К-700А «Кировец». **Узел:** сиденье водителя и
#   его амортизатор. **Тип статьи:** обзор узла с инструкцией по
#   регулировке и ремонту. **Что читатель сможет сделать:** ..."
# Это тоже заметки для редакции, не для читателя, и такой абзац целиком
# нужно вырезать из тела — независимо от того, где именно в документе он
# встретился. Отличаем его от обычного текста по тому, что ВЕСЬ абзац
# состоит только из пар "**Метка:** значение" (используя тот же список
# известных меток, что и для служебного блока в начале документа, плюс
# несколько меток, которые встречаются только в таких "внутренних"
# пометках).
_BODY_NOISE_LABELS = _KNOWN_LABELS + ["Модель", "Узел", "Что читатель сможет сделать"]
_BODY_NOISE_LABEL_ALT = "|".join(re.escape(label) for label in _BODY_NOISE_LABELS)
_NOISE_PARAGRAPH_RE = re.compile(
    rf"^(?:\*\*(?:{_BODY_NOISE_LABEL_ALT})\:?\*\*[^*\n]*)+$",
    re.IGNORECASE,
)


def _strip_leaked_metadata_paragraphs(body: str) -> str:
    blocks = re.split(r"\n\s*\n", body)
    kept = [b for b in blocks if not _NOISE_PARAGRAPH_RE.match(b.strip())]
    return "\n\n".join(kept).strip()


# --- Обрезка по FAQ -------------------------------------------------------
# Перенос из article-demo: после ответа на последний вопрос FAQ в тело
# статьи ничего попадать не должно — ни "Ключевые слова", ни "Теги", ни
# "Похожие статьи" и т.п., которые редакция иногда дописывает следом в
# том же документе. Если секции FAQ в документе нет — отдаём текст как
# есть (лучше лишнее, чем случайно обрезанный нужный контент).
FAQ_HEADING_RE = re.compile(
    r"^##\s*(?:Частые вопросы|Часто задаваемые вопросы|FAQ)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Вопрос внутри FAQ встречается в двух форматах:
#   а) ### Вопрос текстом (H3)
#   б) **Вопрос текстом жирным?**
_QUESTION_BLOCK_RE = re.compile(r"^(?:###\s+\S.*|\*\*[^\n*]+\*\*)$")


def _is_question_block(block: str) -> bool:
    return bool(_QUESTION_BLOCK_RE.match(block.strip()))


def _extract_faq_pairs(text_after_faq_heading: str) -> tuple[str, str]:
    """
    Собирает FAQ строго как чередование (вопрос, ОДИН абзац-ответ), начиная
    с самого первого блока после заголовка FAQ. Как только встречается
    блок, который в текущей позиции не подходит по схеме — сборка
    останавливается. Возвращает (собранный FAQ, всё остальное, что не
    вошло в FAQ) — что делать с "остальным", решает вызывающий код.
    """
    blocks = re.split(r"\n\s*\n", text_after_faq_heading.strip())
    collected: list[str] = []
    expecting = "question"
    consumed = 0

    for block in blocks:
        if expecting == "question":
            if _is_question_block(block):
                collected.append(block)
                expecting = "answer"
                consumed += 1
            else:
                break
        else:
            if _is_question_block(block):
                break
            collected.append(block)
            expecting = "question"
            consumed += 1

    leftover = "\n\n".join(blocks[consumed:])
    return "\n\n".join(collected), leftover


# Настоящий заголовок следующего раздела — например "# 24. Готовые
# шаблоны" — выглядит как эта регулярка. Этим отличаем "в документе есть
# ещё один настоящий раздел после FAQ" (его нельзя обрезать — см.
# документ "Единые типовые нормы СИЗ", где после FAQ ещё 3 полноценных
# раздела) от "дальше идёт служебный мусор без заголовка" (его как раз и
# нужно обрезать — это и был смысл _trim_to_faq изначально).
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+\S")


def _trim_to_faq(body: str) -> str:
    """Обрезает текст статьи сразу после FAQ — но только если дальше в
    документе нет настоящего следующего раздела (с заголовком); если есть
    — это не служебный мусор, а часть статьи, и трогать её нельзя."""
    faq_match = FAQ_HEADING_RE.search(body)
    if not faq_match:
        return body
    faq_body, leftover = _extract_faq_pairs(body[faq_match.end():])
    heading_line = body[faq_match.start():faq_match.end()]
    before_faq = body[:faq_match.start()].rstrip()

    leftover = leftover.strip()
    if leftover and _HEADING_LINE_RE.match(leftover):
        return f"{before_faq}\n\n{heading_line}\n\n{faq_body}\n\n{leftover}".strip()

    return f"{before_faq}\n\n{heading_line}\n\n{faq_body}".strip()


# --- Изображения ---------------------------------------------------------
# Картинки встречаются в документах ДВУМЯ разными способами (и то, и
# другое нужно поддерживать — заранее не угадать, каким пользовалась
# редакция для конкретной статьи):
#
#   1) Вставлены прямо в .docx — pandoc с --extract-media сохраняет их на
#      диск и подставляет в markdown теги вида
#      <img src="media/image7.png" style="width:5in;height:3in" />.
#      Порядок этих тегов в тексте — это и есть порядок картинок в
#      документе (имя файла image7.png ничего не говорит о порядке — это
#      просто внутренний номер вложения в .docx).
#   2) Ссылкой на Яндекс.Диск (как в article-demo) — либо markdown-ссылкой
#      [текст](url), либо голым URL прямо в тексте. Сам файл при парсинге
#      не скачивается (это отдельный сетевой запрос) — здесь только
#      находим ссылки и заменяем на токены, скачивание — в main.py.
IMG_TAG_RE = re.compile(r'<img\s+src="([^"]+)"[^>]*/?>', re.IGNORECASE)

YANDEX_DISK_DOMAINS = ("disk.yandex.", "disk.360.yandex.", "yadi.sk")

# Любая markdown-ссылка вида [текст](url) с доменом Яндекс.Диска — текст
# внутри скобок может быть каким угодно (например "<u>URL</u>", если
# ссылка в Word была подчёркнута) — важен только href.
INLINE_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\((https?://[^\s\)]+)\)")

# Голый URL без markdown-скобок, вклеенный прямо текстом — например
# приклеенный к заголовку раздела ("## https://disk... Название раздела",
# см. _extract_yandex_links) или обёрнутый в HTML-тег форматирования
# Word ("<u>https://disk...</u>"). "<" исключён из класса символов, чтобы
# совпадение URL останавливалось перед таким тегом, а не поглощало его.
BARE_YANDEX_URL_RE = re.compile(
    r"https?://(?:disk\.(?:360\.)?yandex\.[a-zA-Z.]+|yadi\.sk)/[^\s\)\]\(\[<]+"
)

# Подпись под картинкой вида "*Рис. 7 Название*" или
# "*Рис. 7 [<u>Название товара</u>](https://evosiz.ru/...)*" — номер
# рисунка не нужен на сайте, а текст (в т.ч. ссылка на товар) — нужен.
CAPTION_RE = re.compile(
    r"^\*(?:Рис\.?\s*\d+\.?\s*[-—:]?\s*)(.+?)\*[ \t]*$", re.MULTILINE | re.IGNORECASE
)

_IMG_TOKEN_TEMPLATE = "\x00IMG{}\x00"
_IMG_TOKEN_RE = re.compile(r"\x00IMG(\d+)\x00")


@dataclass
class ExtractedImage:
    data: bytes | None = None       # уже готовые байты (встроенная картинка)
    mime: str = "image/jpeg"
    yandex_url: str | None = None   # ссылка на Яндекс.Диск — скачивается отдельно (main.py)


def _extract_yandex_links(content: str, images: list[ExtractedImage]) -> str:
    """Заменяет ссылки на Яндекс.Диск (markdown-ссылкой или голым текстом)
    на плейсхолдеры \\x00IMGn\\x00, добавляя записи в общий список images
    (без скачивания — только сама ссылка)."""

    def _is_yandex(url: str) -> bool:
        return any(domain in url for domain in YANDEX_DISK_DOMAINS)

    def _replace_bracket_link(m: re.Match) -> str:
        url = m.group(1).rstrip("\\")
        if not _is_yandex(url):
            return m.group(0)  # не наша ссылка — не трогаем (например "Также по теме")
        images.append(ExtractedImage(yandex_url=url))
        return _IMG_TOKEN_TEMPLATE.format(len(images) - 1)

    content = INLINE_MD_LINK_RE.sub(_replace_bracket_link, content)
    # если ссылка была единственным содержимым заголовка ("## [url](url)"),
    # убираем осиротевшие "## " перед токеном — картинка не должна быть заголовком
    content = re.sub(r"^#{1,6}[ \t]*(\x00IMG\d+\x00)[ \t]*$", r"\1", content, flags=re.MULTILINE)

    new_lines = []
    for line in content.split("\n"):
        matches = list(BARE_YANDEX_URL_RE.finditer(line))
        if not matches:
            new_lines.append(line)
            continue

        for m in matches:
            images.append(ExtractedImage(yandex_url=m.group(0).rstrip("\\")))

        # убираем саму ссылку из строки — то, что останется (например текст
        # заголовка раздела, если ссылка была приклеена к нему через
        # Shift+Enter в Word) станет отдельной строкой ПОСЛЕ токена картинки
        cleaned = BARE_YANDEX_URL_RE.sub("", line)
        cleaned = _strip_html(cleaned)  # убрать осиротевший <u></u> и т.п.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

        for i in range(len(matches)):
            new_lines.append(_IMG_TOKEN_TEMPLATE.format(len(images) - len(matches) + i))

        if cleaned and not re.match(r"^#{1,6}$", cleaned):
            new_lines.append(cleaned)

    return "\n".join(new_lines)


def _extract_images(content: str, media_dir: Path) -> tuple[str, list[ExtractedImage]]:
    images: list[ExtractedImage] = []

    def _replace(m: re.Match) -> str:
        rel_path = m.group(1)
        img_path = media_dir / rel_path
        if not img_path.exists():
            return ""  # файл почему-то не извлёкся — не оставляем битую ссылку
        mime = mimetypes.guess_type(img_path.name)[0] or "image/png"
        images.append(ExtractedImage(data=img_path.read_bytes(), mime=mime))
        return _IMG_TOKEN_TEMPLATE.format(len(images) - 1)

    content = IMG_TAG_RE.sub(_replace, content)
    content = _extract_yandex_links(content, images)
    content = CAPTION_RE.sub(lambda m: f"*{_strip_html(m.group(1)).strip()}*", content)
    return content, images


# --- Таблицы: если в ячейках Word-таблицы сложное форматирование (свой
# стиль абзаца, отступы и т.п.), pandoc вместо чистого markdown-синтаксиса
# "| a | b |" выдаёт сырой HTML <table>...</table>, из-за чего в редакторе
# статья выглядит как каша из тегов и "пустых строк". Разбираем такой HTML
# сами и собираем из него обычную GFM pipe-таблицу. Замечено, что pandoc
# иногда помечает тегом <th> не только заголовок, но и строки с данными —
# поэтому заголовочной считаем строку только если она лежит внутри <thead>,
# а не по имени тега ячейки.
_RAW_HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)


class _HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.header_row_count = 0
        self._in_thead = False
        self._current_row: list[str] | None = None
        self._in_cell = False
        self._current_cell_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_thead = False
        elif tag == "tr":
            self._current_row = []
            if self._in_thead:
                self.header_row_count += 1
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_cell_parts = []
        elif tag in ("br", "p") and self._in_cell and self._current_cell_parts:
            self._current_cell_parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "thead":
            self._in_thead = False
        elif tag == "tr":
            if self._current_row is not None:
                self.rows.append(self._current_row)
            self._current_row = None
        elif tag in ("td", "th"):
            text = " ".join("".join(self._current_cell_parts).split())
            if self._current_row is not None:
                self._current_row.append(text)
            self._in_cell = False
            self._current_cell_parts = []

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell_parts.append(data)


def _rows_to_markdown_table(rows: list[list[str]], header_row_count: int) -> str:
    rows = [r for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm_rows = [r + [""] * (width - len(r)) for r in rows]

    # Таблица из ОДНОЙ строки — почти никогда не настоящие табличные
    # данные, а "плашка-врезка" (например "Главная мысль статьи", "Не
    # путайте два документа"): в Word её оформляют таблицей с одной
    # ячейкой просто чтобы получить рамку вокруг текста. Как markdown-
    # таблица (заголовок без единой строки данных) это выглядит криво —
    # собираем вместо неё обычную цитату-врезку.
    if len(norm_rows) == 1:
        text = " ".join(c.strip() for c in norm_rows[0] if c.strip())
        return f"> {text}" if text else ""

    def esc(cell: str) -> str:
        return cell.replace("|", "\\|").strip()

    # pandoc иногда кладёт вообще все строки таблицы внутрь <thead> (когда
    # у ячеек сложное форматирование) — тогда header_row_count будет равен
    # общему числу строк, что явно не то, что задумывалось: в реальности
    # заголовок — это всегда только первая строка, а остальное данные.
    header_count = header_row_count or 1
    if header_count >= len(norm_rows):
        header_count = 1
    header_count = min(header_count, len(norm_rows))

    lines = []
    for i in range(header_count):
        lines.append("| " + " | ".join(esc(c) for c in norm_rows[i]) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for r in norm_rows[header_count:]:
        lines.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(lines)


# Обычная ссылка в тексте статьи ("Открыть источник" на НПА и т.п.) иногда
# в Word дополнительно подчёркнута — pandoc тогда даёт не просто
# "[Открыть источник](url)", а "[<u>Открыть источник</u>](url)". Сам html
# в тексте ссылки фронтенд (react-markdown без rehype-raw) не рендерит как
# разметку — он показывает теги буквально, как часть текста ссылки
# ("<u>Открыть источник</u>" прямо на кнопке). Ссылка при этом продолжает
# работать (href не страдает), но выглядит как в баг-репорте — поэтому
# просто убираем теги форматирования из видимого текста ссылки, оставляя
# саму ссылку рабочей.
_MD_LINK_GENERIC_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")


def _clean_markdown_link_labels(content: str) -> str:
    def _replace(m: re.Match) -> str:
        label = _strip_html(m.group(1)).strip()
        href = m.group(2)
        if not label:
            return m.group(0)
        return f"[{label}]({href})"

    return _MD_LINK_GENERIC_RE.sub(_replace, content)


def _convert_raw_html_tables(content: str) -> str:
    def _replace(m: re.Match) -> str:
        html_block = m.group(0)
        parser = _HTMLTableParser()
        try:
            parser.feed(html_block)
        except Exception:
            return html_block
        md_table = _rows_to_markdown_table(parser.rows, parser.header_row_count)
        return md_table if md_table else html_block

    return _RAW_HTML_TABLE_RE.sub(_replace, content)


@dataclass
class ParsedArticle:
    title: str = ""
    seo_title: str = ""
    meta_description: str = ""
    slug: str = ""
    keywords: list[str] = field(default_factory=list)
    content_md: str = ""
    images: list[ExtractedImage] = field(default_factory=list)


def docx_to_raw_markdown(docx_path: str, media_dir: Path) -> str:
    return pypandoc.convert_file(
        docx_path, to="gfm", extra_args=["--wrap=none", f"--extract-media={media_dir}"]
    ).strip()


def parse_article(docx_path: str, media_dir: Path, known_title: str = "") -> ParsedArticle:
    raw = docx_to_raw_markdown(docx_path, media_dir)
    raw = _normalize_raw_markdown(raw)

    extracted_title, body = _extract_title(raw)
    title = (known_title or "").strip() or extracted_title
    # заголовок должен присутствовать в самом теле статьи (от названия
    # включительно до FAQ включительно), поэтому возвращаем его обратно
    # первой строкой — независимо от того, пришёл ли он из документа
    # или был передан снаружи (known_title, например из таблицы Google Диска)
    if title:
        body = f"# {title}\n\n{body.lstrip()}"

    body = _strip_leaked_metadata_paragraphs(body)
    body = _trim_to_faq(body)
    body, images = _extract_images(body, media_dir)
    body = _convert_raw_html_tables(body)
    body = _clean_markdown_link_labels(body)

    # Таблица "SEO-паспорт статьи" проверяется ПЕРВОЙ, раньше абзацного
    # формата (SEO_TITLE_RE/META_DESCRIPTION_RE): у абзацного формата нет
    # собственного ограничителя на "|", и если значение внутри таблицы само
    # содержит двоеточие (например "Единые типовые нормы СИЗ: ЕТН по..."),
    # DOTALL-паттерн SEO_TITLE_RE принимает это двоеточие за разделитель
    # "метка: значение" и утекает (из-за отсутствия пустых строк между
    # строками таблицы) вплоть до конца всей таблицы, склеивая туда же
    # соседние поля. Строгий табличный паттерн с привязкой к "|" такой
    # ошибки не допускает, поэтому должен проверяться раньше.
    seo_title = (
        _strip_char_count_suffix(_extract_field(TABLE_SEO_TITLE_PASSPORT_RE, raw))
        or _extract_field(SEO_TITLE_RE, raw)
        or _extract_field(TABLE_SEO_TITLE_RE, raw)
        or title
    )
    meta_description = (
        _strip_char_count_suffix(_extract_field(TABLE_META_DESCRIPTION_PASSPORT_RE, raw))
        or _extract_field(META_DESCRIPTION_RE, raw)
        or _extract_field(TABLE_META_DESCRIPTION_RE, raw)
    )
    keywords = _extract_keywords(raw)
    # "SEO-паспорт статьи" иногда прямо даёт готовый чистый slug — если он
    # есть, используем его вместо автогенерации из заголовка (но всё равно
    # прогоняем через slugify_ru, чтобы гарантировать валидный kebab-case
    # даже если редактор вписал его с пробелами/кириллицей).
    slug_field = _extract_field(TABLE_SLUG_RE, raw)

    return ParsedArticle(
        title=title,
        seo_title=seo_title,
        meta_description=meta_description,
        slug=slugify_ru(slug_field or seo_title or title),
        keywords=keywords,
        content_md=body.strip(),
        images=images,
    )


def replace_image_placeholders(content_md: str, srcs: list[str | None]) -> str:
    """Заменяет плейсхолдеры \\x00IMGn\\x00 на настоящий markdown-тег картинки."""

    def _replace(match: re.Match) -> str:
        idx = int(match.group(1))
        src = srcs[idx] if idx < len(srcs) else None
        return f"![]({src})" if src else ""

    return _IMG_TOKEN_RE.sub(_replace, content_md)
