import asyncio
from app.celery_app import celery_app
from app.services.research_service import get_research_service

@celery_app.task(bind=True)
def run_background_research(self, research_id: str, query: str):
    """
    Background task to run web research and AI synthesis asynchronously.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _execute_research(research_id, query))
                return future.result()
        else:
            return loop.run_until_complete(_execute_research(research_id, query))
    except RuntimeError:
        # Agar current thread mein event loop nahi hai (jaise AnyIO worker thread mein), toh naya loop bana lo
        return asyncio.run(_execute_research(research_id, query))

async def _execute_research(research_id: str, query: str):
    # Yahan actual research service call hogi
    service = get_research_service()
    await service.run_research(research_id, query)