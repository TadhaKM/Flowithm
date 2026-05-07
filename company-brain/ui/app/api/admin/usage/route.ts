// Aggregates api_requests + skills into the dashboard's usage stats.
// Reads Supabase server-side (service key) and returns:
//   { total_30d, avg_response_ms_30d, most_queried_process, daily_14d: [{date, count}] }
import { NextResponse } from "next/server";

import { getSupabase } from "../../../../lib/supabase";

const DAYS_RANGE_STATS = 30;
const DAYS_RANGE_CHART = 14;

type RequestRow = {
  endpoint: string;
  query_text: string | null;
  matched_skill_id: string | null;
  response_time_ms: number | null;
  created_at: string;
};

// Bucket an ISO timestamp into a YYYY-MM-DD date in the client's local
// timezone. `offsetMin` is `Date.prototype.getTimezoneOffset()` from the
// browser — minutes WEST of UTC (so Dublin IST = -60). We can't trust the
// server's TZ; the dashboard always sends ?tz_offset_min so the buckets
// line up with what the user sees on their wall clock.
function localDateKey(iso: string, offsetMin: number): string {
  const utcMs = Date.parse(iso);
  if (!Number.isFinite(utcMs)) return iso.slice(0, 10);
  // Local time = UTC - offsetMin (offsetMin is "minutes WEST of UTC").
  const localMs = utcMs - offsetMin * 60_000;
  return new Date(localMs).toISOString().slice(0, 10);
}

function emptyDays(n: number, offsetMin: number): { date: string; count: number }[] {
  const out: { date: string; count: number }[] = [];
  // "Today" in the client's local timezone — same shift trick as above.
  const nowLocalMs = Date.now() - offsetMin * 60_000;
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(nowLocalMs - i * 86_400_000);
    out.push({ date: d.toISOString().slice(0, 10), count: 0 });
  }
  return out;
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const offsetMin = parseInt(searchParams.get("tz_offset_min") || "0", 10);
  const safeOffset = Number.isFinite(offsetMin) ? offsetMin : 0;
  const supa = getSupabase();
  const since30 = new Date(Date.now() - DAYS_RANGE_STATS * 86_400_000).toISOString();

  // Pull only what we need; api_requests can grow large fast.
  const { data, error } = await supa
    .from("api_requests")
    .select("endpoint,query_text,matched_skill_id,response_time_ms,created_at")
    .gte("created_at", since30)
    .order("created_at", { ascending: false })
    .limit(10000);

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const rows = (data || []) as RequestRow[];
  const total_30d = rows.length;

  const responseTimes = rows
    .map((r) => r.response_time_ms)
    .filter((x): x is number => typeof x === "number");
  const avg_response_ms_30d =
    responseTimes.length > 0
      ? Math.round(responseTimes.reduce((a, b) => a + b, 0) / responseTimes.length)
      : 0;

  // Most queried process: count by matched_skill_id, then look up its process_name.
  const skillCounts = new Map<string, number>();
  for (const r of rows) {
    if (r.matched_skill_id) {
      skillCounts.set(r.matched_skill_id, (skillCounts.get(r.matched_skill_id) || 0) + 1);
    }
  }
  let most_queried_process: { id: string; process: string; count: number } | null = null;
  if (skillCounts.size > 0) {
    const topId = [...skillCounts.entries()].sort((a, b) => b[1] - a[1])[0][0];
    const topCount = skillCounts.get(topId) || 0;
    const { data: skill } = await supa
      .from("skills")
      .select("id,process_name")
      .eq("id", topId)
      .single();
    most_queried_process = {
      id: topId,
      process: skill?.process_name || "(unknown)",
      count: topCount,
    };
  }

  // 14-day bucketed counts in client-local time. `since14Iso` is a UTC
  // window cutoff that's deliberately a few hours wider than DAYS_RANGE_CHART
  // so events near a day boundary don't get missed when shifted into local time.
  const days = emptyDays(DAYS_RANGE_CHART, safeOffset);
  const dayIndex = new Map(days.map((d, i) => [d.date, i]));
  const since14Iso = new Date(Date.now() - (DAYS_RANGE_CHART + 1) * 86_400_000).toISOString();
  for (const r of rows) {
    if (r.created_at >= since14Iso) {
      const key = localDateKey(r.created_at, safeOffset);
      const i = dayIndex.get(key);
      if (i !== undefined) days[i].count += 1;
    }
  }

  return NextResponse.json({
    total_30d,
    avg_response_ms_30d,
    most_queried_process,
    daily_14d: days,
  });
}
