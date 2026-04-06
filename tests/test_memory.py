"""Tests for memory system: lessons and wiki."""

import json
import pytest
from pathlib import Path

from joshua.memory.lessons import extract_lessons, build_memory_prompt, load_evolved_guidelines
from joshua.memory.wiki import (
    save_raw, write_entry, search_entries, build_wiki_context,
    count_raw_pending, list_entries, _slugify,
)


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("Fix bug #123!") == "fix-bug-123"

    def test_max_length(self):
        long = "a" * 200
        assert len(_slugify(long)) <= 80

    def test_empty(self):
        assert _slugify("") == ""

    def test_unicode(self):
        result = _slugify("caf\u00e9 latt\u00e9")
        assert "caf" in result


class TestLessons:
    def test_extract_lessons_saves_file(self, tmp_dir):
        extract_lessons(
            agent_name="dev",
            task="fix login bug",
            output="Found error in auth.py line 42. Fixed null check.",
            success=True,
            cycle=1,
            state_dir=tmp_dir,
        )
        path = tmp_dir / "memory" / "dev.json"
        assert path.exists()
        lessons = json.loads(path.read_text())
        assert len(lessons) >= 1
        assert lessons[0]["cycle"] == 1
        assert lessons[0]["success"] is True

    def test_extract_lessons_captures_errors(self, tmp_dir):
        output = "Error: database connection failed\nBug in parser.py\nFixed the issue"
        extract_lessons("dev", "scan bugs", output, True, 1, tmp_dir)
        lessons = json.loads((tmp_dir / "memory" / "dev.json").read_text())
        assert len(lessons[0]["errors_found"]) > 0

    def test_extract_lessons_max_limit(self, tmp_dir):
        for i in range(40):
            extract_lessons(
                "dev", f"task {i}",
                f"Error found in file{i}.py and fixed it",
                True, i, tmp_dir,
            )
        lessons = json.loads((tmp_dir / "memory" / "dev.json").read_text())
        assert len(lessons) <= 30

    def test_extract_lessons_skips_empty_output(self, tmp_dir):
        extract_lessons("dev", "nothing", "all good, no issues", True, 1, tmp_dir)
        path = tmp_dir / "memory" / "dev.json"
        # File might not exist if nothing was extracted
        if path.exists():
            lessons = json.loads(path.read_text())
            # Only saved if errors or patterns were found
            for lesson in lessons:
                assert lesson["errors_found"] or lesson["patterns_good"]

    def test_build_memory_prompt_empty(self, tmp_dir):
        result = build_memory_prompt("nonexistent", tmp_dir)
        assert result == ""

    def test_build_memory_prompt_with_data(self, tmp_dir):
        # Create some lessons
        for i in range(3):
            extract_lessons(
                "dev", f"task {i}",
                f"Error: bug #{i} in module.py line {i*10}",
                True, i, tmp_dir,
            )
        result = build_memory_prompt("dev", tmp_dir)
        assert "RECENT LEARNINGS" in result

    def test_load_evolved_guidelines(self, tmp_dir):
        evolved_dir = tmp_dir / "memory" / "evolved"
        evolved_dir.mkdir(parents=True)
        (evolved_dir / "dev.md").write_text("# Guidelines\n- Always test first")

        result = load_evolved_guidelines("dev", tmp_dir)
        assert "Always test first" in result

    def test_load_evolved_guidelines_missing(self, tmp_dir):
        result = load_evolved_guidelines("nonexistent", tmp_dir)
        assert result == ""


class TestWiki:
    def test_save_raw(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        save_raw("dev", 1, "fix auth", "Found bug in auth.py", "myproject", wiki_dir)

        raw_dir = Path(wiki_dir) / "raw"
        files = list(raw_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "agent: dev" in content
        assert "Found bug" in content

    def test_write_entry(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        write_entry(
            "auth system",
            "The auth uses JWT tokens.",
            project="myproject",
            tags=["auth", "security"],
            wiki_dir=wiki_dir,
        )

        entries_dir = Path(wiki_dir) / "entries"
        files = list(entries_dir.glob("*.md"))
        assert len(files) == 1
        assert "myproject--auth-system.md" in files[0].name
        content = files[0].read_text()
        assert "JWT tokens" in content
        assert "auth, security" in content

    def test_write_entry_updates_existing(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        write_entry("topic", "version 1", project="p", wiki_dir=wiki_dir)
        write_entry("topic", "version 2", project="p", wiki_dir=wiki_dir)

        entries = list((Path(wiki_dir) / "entries").glob("*.md"))
        assert len(entries) == 1  # Same file, updated
        assert "version 2" in entries[0].read_text()

    def test_search_entries(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        write_entry("database optimization", "Use indexes on foreign keys.", project="app", wiki_dir=wiki_dir)
        write_entry("auth flow", "JWT with refresh tokens.", project="app", wiki_dir=wiki_dir)

        results = search_entries("database", project="app", wiki_dir=wiki_dir)
        assert len(results) == 1
        assert "database" in results[0]["file"]

    def test_search_entries_no_match(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        write_entry("auth", "JWT tokens", project="app", wiki_dir=wiki_dir)
        results = search_entries("kubernetes", wiki_dir=wiki_dir)
        assert len(results) == 0

    def test_build_wiki_context(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        write_entry("deploy rules", "Always use docker compose.", project="app", wiki_dir=wiki_dir)
        write_entry("auth patterns", "Use JWT with rotation.", project="app", wiki_dir=wiki_dir)

        ctx = build_wiki_context("app", "deploy the service", wiki_dir)
        assert "WIKI KNOWLEDGE BASE" in ctx
        assert "docker compose" in ctx

    def test_build_wiki_context_empty(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        ctx = build_wiki_context("app", "anything", wiki_dir)
        assert ctx == ""

    def test_count_raw_pending(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        save_raw("dev", 1, "task1", "output1", "app", wiki_dir)
        save_raw("dev", 2, "task2", "output2", "app", wiki_dir)
        assert count_raw_pending(wiki_dir) == 2

    def test_list_entries(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        write_entry("topic1", "content1", project="app", wiki_dir=wiki_dir)
        write_entry("topic2", "content2", project="app", wiki_dir=wiki_dir)
        write_entry("other", "content3", project="other", wiki_dir=wiki_dir)

        all_entries = list_entries(wiki_dir=wiki_dir)
        assert len(all_entries) == 3

        app_entries = list_entries(project="app", wiki_dir=wiki_dir)
        assert len(app_entries) == 2

    def test_wiki_context_max_length(self, tmp_dir):
        wiki_dir = str(tmp_dir / "wiki")
        # Write a very large entry
        big_content = "x" * 10000
        write_entry("big topic", big_content, project="app", wiki_dir=wiki_dir)

        ctx = build_wiki_context("app", "big", wiki_dir)
        assert len(ctx) <= 3000

    def test_no_wiki_dir(self):
        """All wiki functions handle empty wiki_dir gracefully."""
        save_raw("dev", 1, "task", "output", "app", "")
        write_entry("topic", "content", wiki_dir="")
        assert search_entries("query", wiki_dir="") == []
        assert build_wiki_context("app", "task", "") == ""
        assert count_raw_pending("") == 0
        assert list_entries(wiki_dir="") == []
