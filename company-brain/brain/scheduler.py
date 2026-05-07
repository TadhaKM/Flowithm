"""APScheduler-driven continuous ingestion.

One BackgroundScheduler instance, started from the FastAPI lifespan, runs
`run_ingest_cycle` every $INGEST_SCHEDULE_HOURS (default 24h). Each cycle
walks every active row in `connected_sources`, fetches anything newer than
its `last_synced_at`, runs the ingestor → embedder pipeline (which
de-duplicates via the chunks.content_hash unique index), and writes a
single audit row to `ingest_runs`.

Manual trigger: `POST /ingest/trigger` calls scheduler.trigger_now() which
fires `run_ingest_cycle` in a daemon thread without disturbing the
scheduled cadence.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("flowithm.scheduler")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class IngestionScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            }
        )
        self.last_run_summary: dict[str, Any] | None = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        hours = int(os.getenv("INGEST_SCHEDULE_HOURS", "24"))
        self.scheduler.add_job(
            func=self.run_ingest_cycle,
            trigger=IntervalTrigger(hours=hours),
            id="scheduled_ingest",
            name="Scheduled source ingestion",
            replace_existing=True,
        )
        self.scheduler.start()
        self._started = True
        logger.info("Scheduler started — ingestion every %sh", hours)
        print(f"[Flowithm scheduler] every {hours}h", flush=True)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self.scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("Scheduler shutdown raised: %s", exc)
        self._started = False

    def schedule_hours(self) -> int:
        return int(os.getenv("INGEST_SCHEDULE_HOURS", "24"))

    def next_run_at_iso(self) -> str | None:
        try:
            job = self.scheduler.get_job("scheduled_ingest")
        except Exception:
            return None
        if not job or not job.next_run_time:
            return None
        return job.next_run_time.astimezone(timezone.utc).isoformat()

    def trigger_now(self) -> None:
        """Fire-and-forget manual run — does NOT replace the cron cadence."""
        threading.Thread(target=self.run_ingest_cycle, daemon=True).start()

    # ------------------------------------------------------------------
    # The cycle
    # ------------------------------------------------------------------

    def run_ingest_cycle(self) -> dict[str, Any]:
        """Single ingestion pass. Always non-raising — errors collect into
        results['errors'] so a single bad source can't kill the cycle."""
        # Lazy imports — keep this module importable without optional deps
        # (tests that just import scheduler shouldn't pay for supabase, etc.).
        from brain.embedder import embed_and_store
        from brain.store import (
            insert_ingest_run,
            list_active_connected_sources,
            update_source_last_synced,
        )
        from ingest.ingest_notion import NotionIngestor
        from ingest.ingest_slack import SlackIngestor

        started_at = _now_utc()
        results: dict[str, Any] = {
            "new_chunks": 0,
            "skipped_chunks": 0,
            "new_conflicts": 0,  # always 0 for now — see scheduler module docstring
            "sources_checked": 0,
            "errors": [],
        }
        print(f"[Flowithm scheduler] cycle start {started_at.isoformat()}", flush=True)

        try:
            sources = list_active_connected_sources()
        except Exception as exc:
            err = f"failed to load connected_sources: {exc}"
            logger.error(err)
            results["errors"].append(err)
            sources = []

        for source in sources:
            try:
                results["sources_checked"] += 1
                chunks = self._fetch_chunks_for_source(source, SlackIngestor, NotionIngestor)
                for chunk in chunks:
                    stored_id = embed_and_store(chunk)
                    if stored_id is None:
                        results["skipped_chunks"] += 1
                    else:
                        results["new_chunks"] += 1
                update_source_last_synced(str(source["id"]), _now_utc().isoformat())
            except NotImplementedError as exc:
                msg = f"{source['source_type']} source {source['id']}: {exc}"
                logger.warning(msg)
                results["errors"].append(msg)
            except Exception as exc:
                msg = f"{source['source_type']} source {source['id']}: {exc}"
                logger.exception(msg)
                results["errors"].append(msg)

        duration_seconds = max(0, int((_now_utc() - started_at).total_seconds()))
        summary = {
            **results,
            "started_at": started_at.isoformat(),
            "duration_seconds": duration_seconds,
        }
        self.last_run_summary = summary

        try:
            insert_ingest_run(summary)
        except Exception as exc:
            logger.error("ingest_runs insert failed: %s", exc)

        logger.info(
            "Scheduled ingest complete: %s new chunks, %s skipped, %s conflicts, %s errors — %ss",
            results["new_chunks"],
            results["skipped_chunks"],
            results["new_conflicts"],
            len(results["errors"]),
            duration_seconds,
        )
        print(
            f"[Flowithm scheduler] cycle done — "
            f"{results['new_chunks']} new, "
            f"{results['skipped_chunks']} skipped, "
            f"{len(results['errors'])} errors, "
            f"{duration_seconds}s",
            flush=True,
        )
        return summary

    @staticmethod
    def _fetch_chunks_for_source(
        source: dict[str, Any],
        SlackCls,
        NotionCls,
    ) -> list:
        """Build the right ingestor for this source row and produce chunks."""
        cfg = source.get("config") or {}
        since_iso = source.get("last_synced_at")
        since_dt = _parse_iso(since_iso)

        if source["source_type"] == "slack":
            ingestor = SlackCls(
                token=cfg.get("bot_token"),
                channel_ids=cfg.get("channel_ids") or [],
                since=since_dt,
            )
            return ingestor.process(None)

        if source["source_type"] == "notion":
            ingestor = NotionCls(
                token=cfg.get("integration_token"),
                page_ids=cfg.get("page_ids") or [],
                since=since_dt,
            )
            return ingestor.process(None)

        # Unknown source type — surfaced as an error in the run summary.
        raise NotImplementedError(f"no live ingestor for source_type={source['source_type']!r}")


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


# Module-level singleton — imported by api/main.py lifespan + the trigger endpoint.
scheduler = IngestionScheduler()
