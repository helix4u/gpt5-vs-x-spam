import asyncio
from typing import List, Optional, Callable
from .types import Profile
from .scraper_sync import scrape_search_users_sync


async def scrape_search_users(
    query: str,
    max_results: int = 40,
    on_new: Optional[Callable[[List[Profile], int, int], None]] = None,
    on_evt: Optional[Callable[[dict], None]] = None,
) -> List[Profile]:
    # Run the synchronous scraper in a worker thread to avoid Windows asyncio
    # subprocess limitations and simplify browser lifecycle.
    return await asyncio.to_thread(
        scrape_search_users_sync,
        query,
        max_results,
        on_new,
        on_evt,
    )
