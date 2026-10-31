# --- НАДІЙНИЙ АСИНХРОННИЙ МЕДІАКАСКАД ---

async def media_tier_1_wikipedia(session: aiohttp.ClientSession, query: str) -> str:
    """Шукає якісне фото через офіційне API Вікіпедії (англійської чи української)."""
    for lang in ['en', 'uk']:
        try:
            search_url = f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={aiohttp.helpers.quote(query)}&format=json"
            async with session.get(search_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5) as resp:
                data = await resp.json()
                items = data.get('query', {}).get('search', [])
                if items:
                    title = items[0]['title']
                    img_url_api = f"https://{lang}.wikipedia.org/w/api.php?action=query&prop=pageimages&piprop=original&titles={aiohttp.helpers.quote(title)}&format=json"
                    async with session.get(img_url_api, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5) as img_resp:
                        img_data = await img_resp.json()
                        pages = img_data.get('query', {}).get('pages', {})
                        for _, pinfo in pages.items():
                            if 'original' in pinfo and pinfo['original'].get('source'):
                                return pinfo['original']['source']
        except Exception:
            continue
    return None

async def media_tier_2_wikimedia(session: aiohttp.ClientSession, query: str) -> str:
    """Шукає зображення у Wikimedia Commons за ключовими словами."""
    try:
        api_url = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={aiohttp.helpers.quote(query)}&gsrnamespace=6&gsrlimit=3&prop=imageinfo&iiprop=url&format=json"
        async with session.get(api_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5) as resp:
            data = await resp.json()
            pages = data.get('query', {}).get('pages', {})
            for _, page_info in pages.items():
                imageinfo = page_info.get('imageinfo', [])
                if imageinfo and imageinfo[0].get('url'):
                    url = imageinfo[0].get('url')
                    if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png']):
                        return url
    except Exception:
        pass
    return None

async def media_tier_3_picsum(query: str) -> str:
    """Гарантований запасний варіант (красивий генератор за хешем запиту)."""
    seed = abs(hash(query.lower())) % 10000
    return f"https://picsum.photos/seed/{seed}/1200/800"

async def find_image_url_from_web(query: str) -> str:
    clean_query = query.strip()
    log_event("MEDIA_CASCADE", f"Запуск пошуку для: '{clean_query}'")

    async with aiohttp.ClientSession() as session:
        # Рівень 1: Wikipedia (найкраще для реальних об'єктів на кшталт 'Пентагон')
        url = await media_tier_1_wikipedia(session, clean_query)
        if url:
            log_event("CASCADE_SUCCESS", f"Знайдено в Wikipedia: {url}")
            return url

        # Рівень 2: Wikimedia Commons
        url = await media_tier_2_wikimedia(session, clean_query)
        if url:
            log_event("CASCADE_SUCCESS", f"Знайдено в Wikimedia: {url}")
            return url

        # Рівень 3: Запасний якісний сервіс
        fallback = await media_tier_3_picsum(clean_query)
        log_event("CASCADE_FALLBACK", f"Використано резерв: {fallback}")
        return fallback
