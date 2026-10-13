"""Разбор ссылки на видео (YouTube / Rutube / Vimeo) в пару (type, id)
для компонента bottomVideo в Strapi: type — одно из youtube / rutube / vimeo,
value — только id видео, без ссылки."""
import re
from urllib.parse import parse_qs, urlparse

_YT_ID = r"[A-Za-z0-9_-]{11}"


def parse_video_url(url: str) -> tuple[str, str] | None:
    """Возвращает (type, id) или None, если ссылка не распознана."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = parsed.path

    if host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if not re.fullmatch(_YT_ID, video_id):
            m = re.match(rf"/(?:shorts|embed|live|v)/({_YT_ID})", path)
            video_id = m.group(1) if m else ""
        return ("youtube", video_id) if video_id else None

    if host == "youtu.be":
        m = re.match(rf"/({_YT_ID})", path)
        return ("youtube", m.group(1)) if m else None

    if host == "rutube.ru":
        # /video/<id>/, /video/private/<id>/, /play/embed/<id>
        m = re.match(r"/(?:video(?:/private)?|play/embed)/([0-9a-fA-F]{32})", path)
        return ("rutube", m.group(1)) if m else None

    if host in ("vimeo.com", "player.vimeo.com"):
        # /123456789, /channels/x/123456789, /video/123456789 (у скрытых видео
        # после id может идти hash — в value нужен только сам id)
        m = re.search(r"/(\d{6,})(?:/|$)", path)
        return ("vimeo", m.group(1)) if m else None

    return None
