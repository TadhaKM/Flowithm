"""ingest.* — chunk-builder pure logic for each source type."""
from ingest.ingest_github import GitHubIngestor, format_issue
from ingest.ingest_notion import NotionIngestor, parse_sections
from ingest.ingest_slack import SlackIngestor, group_threads


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def test_slack_group_threads_separates_independent_messages():
    messages = [
        {"channel": "general", "user": "u1", "ts": "1.0", "text": "hi"},
        {"channel": "general", "user": "u2", "ts": "2.0", "text": "hello"},
    ]
    groups = group_threads(messages)
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_slack_group_threads_combines_replies_under_parent():
    messages = [
        {"channel": "g", "user": "u1", "ts": "1.0", "text": "parent"},
        {"channel": "g", "user": "u2", "ts": "2.0", "thread_ts": "1.0", "text": "reply 1"},
        {"channel": "g", "user": "u3", "ts": "3.0", "thread_ts": "1.0", "text": "reply 2"},
    ]
    groups = group_threads(messages)
    assert len(groups) == 1
    thread = groups[0]
    assert len(thread) == 3


def test_slack_group_threads_sorted_by_ts():
    messages = [
        {"channel": "g", "user": "u3", "ts": "3.0", "thread_ts": "1.0", "text": "third"},
        {"channel": "g", "user": "u1", "ts": "1.0", "text": "parent"},
        {"channel": "g", "user": "u2", "ts": "2.0", "thread_ts": "1.0", "text": "second"},
    ]
    groups = group_threads(messages)
    thread = groups[0]
    assert [m["ts"] for m in thread] == ["1.0", "2.0", "3.0"]


def test_slack_build_chunks_marks_source_and_metadata():
    messages = [
        {"channel": "general", "user": "alice", "ts": "100.0", "text": "lorem ipsum dolor sit amet"}
    ]
    chunks = SlackIngestor().build_chunks(messages)
    assert len(chunks) >= 1
    c = chunks[0]
    assert c.source_type == "slack"
    assert c.source_name == "general"
    assert c.metadata["author"] == "alice"
    assert c.metadata["timestamp"] == "100.0"


def test_slack_build_chunks_marks_thread_metadata_when_threaded():
    messages = [
        {"channel": "g", "user": "u1", "ts": "1.0", "text": "parent message body"},
        {"channel": "g", "user": "u2", "ts": "2.0", "thread_ts": "1.0", "text": "reply body"},
    ]
    chunks = SlackIngestor().build_chunks(messages)
    # Both messages collapse into one chunk (thread)
    assert len(chunks) == 1
    assert chunks[0].metadata.get("thread_ts") == "1.0"


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

def test_notion_parse_sections_splits_h1_and_h2():
    md = (
        "# Page A\n\nIntro for A\n\n"
        "## Section A1\n\nContent of A1\n\n"
        "# Page B\n\nIntro for B\n\n"
        "## Section B1\n\nContent of B1\n"
    )
    sections = parse_sections(md)
    headings = [s["heading"] for s in sections]
    assert headings == ["Page A", "Section A1", "Page B", "Section B1"]


def test_notion_section_tracks_parent_h1_as_page_title():
    md = "# Page X\n\n## S1\n\nbody1\n\n## S2\n\nbody2\n"
    sections = parse_sections(md)
    s1 = next(s for s in sections if s["heading"] == "S1")
    s2 = next(s for s in sections if s["heading"] == "S2")
    assert s1["page_title"] == "Page X"
    assert s2["page_title"] == "Page X"


def test_notion_h1_intro_section_has_its_own_body():
    md = "# Page X\n\nIntro paragraph.\n\n## S1\n\nbody\n"
    sections = parse_sections(md)
    page_x = next(s for s in sections if s["heading"] == "Page X")
    assert "Intro paragraph" in page_x["body"]


def test_notion_h3_does_not_split_section():
    """H3+ should stay inside the parent H2 body, not become its own section."""
    md = "# Page\n\n## S1\n\n### H3 inside\n\nh3 body\n\n## S2\n\nbody\n"
    sections = parse_sections(md)
    headings = [s["heading"] for s in sections]
    assert "H3 inside" not in headings
    s1 = next(s for s in sections if s["heading"] == "S1")
    assert "H3 inside" in s1["body"]


def test_notion_process_skips_empty_sections():
    """Sections with empty bodies are dropped by validate() (e.g. an H1 immediately followed by H2)."""
    md = "# Page\n\n## S1\n\n## S2\n\nactual body content here please\n"
    chunks = NotionIngestor().process(md)
    sources = [c.source_name for c in chunks]
    assert "S1" not in sources  # empty body, dropped by validate()
    assert "S2" in sources


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _basic_issue(**overrides):
    base = {
        "number": 42,
        "title": "Test issue",
        "state": "open",
        "labels": [],
        "created_at": "2026-01-01T00:00:00Z",
        "body": "Issue body content here.",
        "comments": [],
    }
    base.update(overrides)
    return base


def test_github_format_issue_includes_header_and_body():
    out = format_issue(_basic_issue())
    assert "Issue #42: Test issue" in out
    assert "Issue body content here." in out


def test_github_format_issue_appends_comments():
    issue = _basic_issue(
        comments=[
            {"user": "alice", "created_at": "2026-01-02T00:00:00Z", "body": "first comment"},
            {"user": "bob", "created_at": "2026-01-03T00:00:00Z", "body": "second comment"},
        ],
    )
    out = format_issue(issue)
    assert "Comment by alice" in out
    assert "first comment" in out
    assert "Comment by bob" in out
    assert "second comment" in out


def test_github_build_chunks_one_per_issue():
    issues = [
        _basic_issue(number=1, title="A"),
        _basic_issue(number=2, title="B", state="closed", labels=["bug"]),
    ]
    chunks = GitHubIngestor().build_chunks(issues)
    assert len(chunks) == 2
    assert chunks[0].source_name == "#1: A"
    assert chunks[0].metadata["state"] == "open"
    assert chunks[1].metadata["state"] == "closed"
    assert chunks[1].metadata["labels"] == ["bug"]


def test_github_chunks_marked_as_github_source():
    chunks = GitHubIngestor().build_chunks([_basic_issue()])
    assert chunks[0].source_type == "github"
