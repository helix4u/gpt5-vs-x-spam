import asyncio
from typing import List
from .types import Profile
from .scraper_sync import scrape_search_users_sync


async def scrape_search_users(query: str, max_results: int = 40) -> List[Profile]:
    # Run the synchronous scraper in a worker thread to avoid Windows asyncio
    # subprocess limitations and simplify browser lifecycle.
    return await asyncio.to_thread(scrape_search_users_sync, query, max_results)
