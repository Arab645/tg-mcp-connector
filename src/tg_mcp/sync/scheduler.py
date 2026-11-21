# src/tg_mcp/sync/scheduler.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .engine import run_sync
from ..summarize.pipeline import run_pending_summaries

def _today_and_yesterday() -> list[str]:
    now = datetime.now(timezone.utc).date()
    return [now.isoformat(), (now - timedelta(days=1)).isoformat()]

async def tick(ctx, dates: list[str] | None = None) -> dict:
    dates = dates or _today_and_yesterday()
    sync_report = await run_sync(ctx.conn, ctx.tg, max_backfill_days=getattr(ctx.settings, "max_backfill_days", None))
    summaries = await run_pending_summaries(
        ctx.conn, ctx.claude, ctx.notion, ctx.settings.summary_model, dates)
    return {"sync": sync_report, "summaries": summaries}

def start_scheduler(ctx) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(tick, "interval",
                  minutes=ctx.settings.sync_interval_min,
                  args=[ctx], coalesce=True, max_instances=1,
                  next_run_time=datetime.now(timezone.utc))
    sched.start()
    return sched
