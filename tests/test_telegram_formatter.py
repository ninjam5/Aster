import pytest
from tools.telegram_formatter import markdown_to_telegram_html, split_telegram_html

def test_markdown_to_telegram_html_basic_formatting():
    raw = "**Ninjam4** — 1:08 PM: Aloha human\n*November 9, 2026*"
    out = markdown_to_telegram_html(raw)
    assert "<b>Ninjam4</b>" in out
    assert "<i>November 9, 2026</i>" in out


def test_markdown_to_telegram_html_code_block():
    raw = "Here is code:\n```python\nprint(1 < 2 & 3)\n```"
    out = markdown_to_telegram_html(raw)
    assert '<pre><code class="language-python">print(1 &lt; 2 &amp; 3)</code></pre>' in out


def test_markdown_to_telegram_html_table_conversion():
    table = """| # | Sender | Time | Message |
|---|--------|------|---------|
| 1 | Ninjam4 | 1:08 PM | Aloha human |
| 2 | Ob | 1:09 PM | hahaha |"""
    out = markdown_to_telegram_html(table)
    assert "<b>Ninjam4</b>" in out
    assert "Aloha human" in out
    assert "<b>Ob</b>" in out
    assert "hahaha" in out


def test_markdown_to_telegram_html_blockquotes():
    raw = """**Ob** — *before 1:24 PM*
> he did figure it out eventually but goddamn it

**Ninjam4** — *1:24 PM*
> Aloha human"""
    out = markdown_to_telegram_html(raw)
    assert "<blockquote>he did figure it out eventually but goddamn it</blockquote>" in out
    assert "<blockquote>Aloha human</blockquote>" in out
    assert "\n\n" in out  # Ensures newlines between messages are preserved


def test_markdown_to_telegram_html_expandable_blockquotes():
    raw = """> line 1
> line 2
> line 3
> line 4
> line 5"""
    out = markdown_to_telegram_html(raw)
    assert "<blockquote expandable>" in out
    assert "</blockquote>" in out


def test_markdown_to_telegram_html_escapes_raw_html_tags():
    raw = "Testing <script>alert(1)</script> & special chars"
    out = markdown_to_telegram_html(raw)
    assert "&lt;script&gt;" in out
    assert "&amp;" in out


def test_split_telegram_html_chunks_long_message():
    long_text = "Paragraph one.\n\n" + ("A" * 3000) + "\n\n" + ("B" * 2000)
    chunks = split_telegram_html(long_text, max_chunk=3500)
    assert len(chunks) == 2
    assert all(len(c) <= 3500 for c in chunks)
