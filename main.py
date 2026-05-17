import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.append(str(Path(__file__).parent.parent))

from src.database.init_db import init_db
from src.tools.sync_calendar import sync_google_calendar
from src.tools.fetch_jira import sync_jira_tasks
from src.orchestrator import start
from src.database.db_utils import (
    is_first_run    
)

async def run():

    init_db()

    # sync if its the first time
    if is_first_run():
        print("run first sync")
        await sync_google_calendar()
        await sync_jira_tasks()
        print("sync complete")

    # 3. Start the orchestrator loop — runs forever
    await start()


if __name__ == "__main__":
    asyncio.run(run())