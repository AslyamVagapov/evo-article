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
