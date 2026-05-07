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
import socket
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

    def run_ingest_cycle(self) -> dict[str, Any] | None:
        """Single ingestion pass. Always non-raising — errors collect into
        results['errors'] so a single bad source can't kill the cycle.

        Multi-worker safe: tries to acquire a singleton DB row-mutex first;
        returns None if another worker is already mid-cycle.
        """
        # Lazy imports — keep this module importable without optional deps
        # (tests that just import scheduler shouldn't pay for supabase, etc.).
        from brain.drift import check_chunks_against_skills
        from brain.embedder import embed_and_store
        from brain.store import (
            get_client,
            insert_ingest_run,
            list_active_connected_sources,
            update_source_last_synced,
        )
        from ingest.ingest_notion import NotionIngestor
        from ingest.ingest_slack import SlackIngestor

        # ---- mutex acquisition ----
        # If the lock RPCs aren't migrated yet, we proceed without locking
        # rather than refusing to ingest — better degraded behaviour than
        # broken behaviour for users mid-migration.
        client = get_client()
        holder = f"{socket.gethostname()}:{os.getpid()}"
        acquired = True
        lock_supported = True
        try:
            resp = client.rpc("try_acquire_ingest_lock", {"holder": holder}).execute()
            acquired = bool(resp.data)
        except Exception as exc:
            print(f"[Flowithm scheduler] lock RPC unavailable, running unlocked: {exc}", flush=True)
            lock_supported = False

        if not acquired:
            print(
                "[Flowithm scheduler] Skipping ingest — lock held by another worker",
                flush=True,
            )
            return None

        started_at = _now_utc()
        results: dict[str, Any] = {
            "new_chunks": 0,
            "skipped_chunks": 0,
            "new_conflicts": 0,
            "sources_checked": 0,
            "errors": [],
        }
        # Collect chunks that actually got embedded this cycle so we can run
        # the chunk-vs-skill drift pass once at the end (single LLM batch
        # instead of per-source — saves Claude calls and surfaces conflicts
        # across the whole sync at once).
        newly_embedded: list = []
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
                        newly_embedded.append(chunk)
                update_source_last_synced(str(source["id"]), _now_utc().isoformat())
            except NotImplementedError as exc:
                msg = f"{source['source_type']} source {source['id']}: {exc}"
                logger.warning(msg)
                results["errors"].append(msg)
            except Exception as exc:
                msg = f"{source['source_type']} source {source['id']}: {exc}"
                logger.exception(msg)
                results["errors"].append(msg)

        # Drift check: only the chunks that were actually new this cycle.
        # If de-dup skipped everything, there's nothing to compare.
        if newly_embedded:
            try:
                conflicts = check_chunks_against_skills(newly_embedded)
                results["new_conflicts"] = len(conflicts)
            except Exception as exc:
                msg = f"check_chunks_against_skills: {exc}"
                logger.error(msg)
                results["errors"].append(msg)

        # Staleness pass — runs every cycle regardless of new chunk count
        # so reviewed_at expiry surfaces even on no-op syncs.
        try:
            from brain.staleness import run_staleness_check

            stale = run_staleness_check()
            results["stale_flagged"] = stale.get("newly_flagged", 0)
            results["stale_cleared"] = stale.get("flags_cleared", 0)
        except Exception as exc:
            msg = f"run_staleness_check: {exc}"
            logger.error(msg)
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

        # Release the mutex. Best-effort — the 15-minute timeout on the
        # acquire side reclaims a stuck lock anyway.
        if lock_supported:
            try:
                client.rpc("release_ingest_lock").execute()
            except Exception as exc:
                logger.warning("release_ingest_lock failed: %s", exc)

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
