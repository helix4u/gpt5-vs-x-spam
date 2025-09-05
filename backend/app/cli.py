import asyncio, json, sys
import typer
from typing import Optional, List
from rich import print as rprint
from .scraper import scrape_search_users
from .classifier import classify_profiles
from .actions import block_handles
from .storage import save_classification


app = typer.Typer(add_completion=False, help="CLI... scrape... classify... block...")


@app.command()
def search(query: str, max_results: int = 40, out: Optional[str] = None, classify: bool = True):
    """Scrape X user search for profiles... optionally classify... write json."""

    async def _run():
        profiles = await scrape_search_users(query, max_results=max_results)
        data = {"profiles": [p.model_dump() for p in profiles]}
        if classify and profiles:
            classes = await classify_profiles(profiles)
            data["classifications"] = [c.model_dump() for c in classes]
            for c in classes:
                save_classification(c)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        rprint(text)

    asyncio.run(_run())


@app.command()
def block(handles: List[str]):
    """Block a list of handles... respects rate limits... ui driven..."""

    async def _run():
        res = await block_handles(handles)
        rprint([r.model_dump() for r in res])

    asyncio.run(_run())


@app.command()
def classify_file(path: str):
    """Classify profiles contained in a json file with key 'profiles'..."""

    async def _run():
        obj = json.load(open(path, "r", encoding="utf-8"))
        profiles = obj.get("profiles", [])
        from .types import Profile

        profs = [Profile(**p) for p in profiles]
        classes = await classify_profiles(profs)
        for c in classes:
            save_classification(c)
        rprint([c.model_dump() for c in classes])

    asyncio.run(_run())


if __name__ == "__main__":
    app()

