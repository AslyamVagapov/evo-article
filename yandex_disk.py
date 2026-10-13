"""
Получение файла по публичной ссылке Яндекс.Диска (disk.yandex.ru,
disk.360.yandex.ru, yadi.sk) без OAuth-токена — работает через
публичный REST API Яндекса для расшаренных ресурсов.

Документация: https://yandex.ru/dev/disk/api/reference/public.html

ВНИМАНИЕ: в этой песочнице нет доступа к cloud-api.yandex.net (сетевые
ограничения), поэтому фактический вызов API здесь не протестирован.
Логика реализована строго по официальной спецификации; проверь на
своей стороне и напиши, если Яндекс 360 (disk.360.yandex.ru) отдаёт
ответ в другом формате — тогда поправим парсинг ответа.
"""
import requests

YANDEX_PUBLIC_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"


class YandexDiskError(Exception):
    pass


def get_direct_download_url(public_url: str) -> str:
    """
    По публичной ссылке на файл/папку Яндекс.Диска возвращает прямой
    URL для скачивания байтов файла.
    """
    response = requests.get(
        YANDEX_PUBLIC_API,
        params={"public_key": public_url},
        timeout=15,
    )
    if response.status_code != 200:
        raise YandexDiskError(
            f"Яндекс.Диск вернул {response.status_code} для {public_url}: {response.text[:200]}"
        )
    data = response.json()
    href = data.get("href")
    if not href:
        raise YandexDiskError(f"В ответе Яндекс.Диска нет поля href: {data}")
    return href


def download_image_bytes(public_url: str) -> bytes:
    """Скачивает байты картинки по публичной ссылке Яндекс.Диска."""
    direct_url = get_direct_download_url(public_url)
    resp = requests.get(direct_url, timeout=30)
    resp.raise_for_status()
    return resp.content
