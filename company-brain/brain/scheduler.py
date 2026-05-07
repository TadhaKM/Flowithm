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
        """Single ingestion pass across every organisation. Multi-tenant:
        groups active connected_sources by org_id and runs one sub-cycle
        per org. Each sub-cycle has its own ingest_runs row, drift pass,
        and staleness pass. Always non-raising — errors collect per-org
        so a single bad source / bad org can't kill the cycle.

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
            all_sources = list_active_connected_sources()  # cross-org
        except Exception as exc:
            err = f"failed to load connected_sources: {exc}"
            logger.error(err)
            results["errors"].append(err)
            all_sources = []

        # Group by org_id. Sources without an org_id (shouldn't happen after
        # the schema backfill, but defensive) get bucketed under the default.
        from brain.store import _default_org_id

        per_org: dict[str, list[dict[str, Any]]] = {}
        for s in all_sources:
            key = str(s.get("org_id") or _default_org_id())
            per_org.setdefault(key, []).append(s)

        # If no orgs have sources, still run a staleness pass for the
        # default org so reviewed_at expiry isn't blocked.
        if not per_org:
            per_org[_default_org_id()] = []

        for org_id, sources in per_org.items():
            org_newly_embedded: list = []
            org_results = {
                "new_chunks": 0,
                "skipped_chunks": 0,
                "new_conflicts": 0,
                "sources_checked": 0,
                "errors": [],
            }
            for source in sources:
                try:
                    org_results["sources_checked"] += 1
                    chunks = self._fetch_chunks_for_source(source)
                    for chunk in chunks:
                        stored_id = embed_and_store(chunk, org_id=org_id)
                        if stored_id is None:
                            org_results["skipped_chunks"] += 1
                        else:
                            org_results["new_chunks"] += 1
                            org_newly_embedded.append(chunk)
                    update_source_last_synced(str(source["id"]), _now_utc().isoformat())
                except NotImplementedError as exc:
                    msg = f"{source['source_type']} source {source['id']}: {exc}"
                    logger.warning(msg)
                    org_results["errors"].append(msg)
                except Exception as exc:
                    msg = f"{source['source_type']} source {source['id']}: {exc}"
                    logger.exception(msg)
                    org_results["errors"].append(msg)

            # Drift on this org's newly-embedded chunks.
            if org_newly_embedded:
                try:
                    conflicts = check_chunks_against_skills(org_newly_embedded, org_id=org_id)
                    org_results["new_conflicts"] = len(conflicts)
                except Exception as exc:
                    msg = f"check_chunks_against_skills: {exc}"
                    logger.error(msg)
                    org_results["errors"].append(msg)

            # Staleness pass per-org.
            org_stale_flagged = 0
            org_stale_cleared = 0
            try:
                from brain.staleness import run_staleness_check

                stale = run_staleness_check(org_id=org_id)
                org_stale_flagged = stale.get("newly_flagged", 0)
                org_stale_cleared = stale.get("flags_cleared", 0)
            except Exception as exc:
                msg = f"run_staleness_check: {exc}"
                logger.error(msg)
                org_results["errors"].append(msg)

            # Per-org ingest_runs row.
            try:
                insert_ingest_run({
                    **org_results,
                    "stale_flagged": org_stale_flagged,
                    "stale_cleared": org_stale_cleared,
                    "started_at": started_at.isoformat(),
                    "duration_seconds": max(0, int((_now_utc() - started_at).total_seconds())),
                }, org_id=org_id)
            except Exception as exc:
                logger.error("ingest_runs insert failed for %s: %s", org_id, exc)

            # Aggregate into the cross-org summary kept in memory for /ingest/status.
            results["sources_checked"] += org_results["sources_checked"]
            results["new_chunks"]      += org_results["new_chunks"]
            results["skipped_chunks"]  += org_results["skipped_chunks"]
            results["new_conflicts"]   += org_results["new_conflicts"]
            results["errors"].extend(f"[{org_id}] {e}" for e in org_results["errors"])
            newly_embedded.extend(org_newly_embedded)

        duration_seconds = max(0, int((_now_utc() - started_at).total_seconds()))
        summary = {
            **results,
            "started_at": started_at.isoformat(),
            "duration_seconds": duration_seconds,
        }
        self.last_run_summary = summary

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
    def _fetch_chunks_for_source(source: dict[str, Any]) -> list:
        """Build the right ingestor for this source row and produce chunks.
        Each branch lazy-imports its ingestor so optional deps (google-*,
        slack_sdk) only matter when that source_type is actually configured."""
        cfg = source.get("config") or {}
        since_dt = _parse_iso(source.get("last_synced_at"))
        stype = source["source_type"]

        if stype == "slack":
            from ingest.ingest_slack import SlackIngestor

            return SlackIngestor(
                token=cfg.get("bot_token"),
                channel_ids=cfg.get("channel_ids") or [],
                since=since_dt,
            ).process(None)

        if stype == "notion":
            from ingest.ingest_notion import NotionIngestor

            return NotionIngestor(
                token=cfg.get("integration_token"),
                page_ids=cfg.get("page_ids") or [],
                since=since_dt,
            ).process(None)

        if stype == "gmail":
            from ingest.ingest_gmail import GmailIngestor

            return GmailIngestor(
                credentials_json=cfg.get("credentials_json"),
                label_filters=cfg.get("label_filters") or [],
                since=since_dt,
                min_thread_length=int(cfg.get("min_thread_length", 2)),
            ).process(None)

        if stype == "intercom":
            from ingest.ingest_intercom import IntercomIngestor

            return IntercomIngestor(
                access_token=cfg.get("access_token"),
                since=since_dt,
                tags=cfg.get("tags"),
                min_message_count=int(cfg.get("min_message_count", 3)),
            ).process(None)

        # Unknown source type — surfaced as an error in the run summary.
        raise NotImplementedError(f"no live ingestor for source_type={stype!r}")


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


# Module-level singleton — imported by api/main.py lifespan + the trigger endpoint.
scheduler = IngestionScheduler()
