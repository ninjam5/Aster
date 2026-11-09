import re
import html

def markdown_to_telegram_html(text: str) -> str:
    """Convert standard LLM markdown output into valid Telegram HTML format.
    
    Handles:
    - Code blocks (```lang ... ```) -> <pre><code class="language-lang">...</code></pre>
    - Inline code (`...`) -> <code>...</code>
    - Blockquotes (> lines) -> <blockquote>...</blockquote> (or <blockquote expandable>...</blockquote>)
    - Bold (**text** or __text__) -> <b>text</b> / <u>text</u>
    - Italic (*text* or _text_) -> <i>text</i>
    - Strikethrough (~~text~~) -> <s>text</s>
    - Links ([text](url)) -> <a href="url">text</a>
    - Converts markdown tables into clean readable list / lines
    - Preserves newlines and escapes bare HTML characters (<, >, &).
    """
    if not text:
        return ""

    # 1. Clean markdown tables into clean aligned lines (Telegram cannot render markdown tables)
    def _convert_tables(t: str) -> str:
        lines = t.split("\n")
        new_lines = []
        in_table = False
        table_headers = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                # Skip separator lines like |---|---|
                if all(re.match(r"^:?-+:?$", c) for c in cells):
                    in_table = True
                    continue
                if not in_table and not table_headers:
                    table_headers = cells
                    continue
                # Table row
                if table_headers and len(table_headers) == len(cells):
                    lower_headers = [h.lower() for h in table_headers]
                    if "sender" in lower_headers and "message" in lower_headers:
                        sender = cells[lower_headers.index("sender")]
                        msg = cells[lower_headers.index("message")]
                        time_str = cells[lower_headers.index("time")] if "time" in lower_headers else ""
                        time_part = f" ({time_str})" if time_str else ""
                        num = f"{cells[lower_headers.index('#')]}. " if "#" in lower_headers else "• "
                        new_lines.append(f"{num}**{sender}**{time_part}: {msg}")
                    else:
                        formatted_row = " • " + " | ".join(
                            f"**{h}**: {c}" for h, c in zip(table_headers, cells) if c
                        )
                        new_lines.append(formatted_row)
                else:
                    new_lines.append(" • " + " | ".join(cells))
            else:
                if in_table:
                    in_table = False
                    table_headers = []
                new_lines.append(line)
        return "\n".join(new_lines)

    text = _convert_tables(text)

    # 2. Extract code blocks
    code_blocks = []
    def _save_code_block(m):
        lang = (m.group(1) or "").strip()
        code = m.group(2).strip("\r\n")
        idx = len(code_blocks)
        esc_code = html.escape(code)
        if lang:
            code_blocks.append(f'<pre><code class="language-{html.escape(lang)}">{esc_code}</code></pre>')
        else:
            code_blocks.append(f"<pre>{esc_code}</pre>")
        return f"@@@CODE_BLOCK_{idx}@@@"

    text = re.sub(r"```([a-zA-Z0-9_\-\+]*)\n?(.*?)```", _save_code_block, text, flags=re.DOTALL)

    # 3. Extract inline code
    inline_codes = []
    def _save_inline_code(m):
        code = m.group(1)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{html.escape(code)}</code>")
        return f"@@@INLINE_CODE_{idx}@@@"

    text = re.sub(r"`([^`\n]+)`", _save_inline_code, text)

    # 4. Extract blockquotes (> lines)
    blockquotes = []
    def _process_blockquotes(t: str) -> str:
        lines = t.split("\n")
        out_lines = []
        current_quote = []

        def flush_quote():
            if not current_quote:
                return
            quote_text = "\n".join(current_quote)
            idx = len(blockquotes)
            esc_quote = html.escape(quote_text)
            esc_quote = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc_quote, flags=re.DOTALL)
            esc_quote = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", esc_quote)
            open_tag = "blockquote expandable" if len(current_quote) >= 4 else "blockquote"
            blockquotes.append(f"<{open_tag}>{esc_quote}</blockquote>")
            out_lines.append(f"@@@BLOCKQUOTE_{idx}@@@")
            current_quote.clear()

        for line in lines:
            m = re.match(r"^\s*>{1,2}\s?(.*)$", line)
            if m:
                current_quote.append(m.group(1))
            else:
                flush_quote()
                out_lines.append(line)
        flush_quote()
        return "\n".join(out_lines)

    text = _process_blockquotes(text)

    # 5. Escape HTML special characters in surrounding text
    text = html.escape(text)

    # 6. Headers (# Heading -> <b>Heading</b>)
    text = re.sub(r"^\s*#{1,6}\s*(.+?)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 7. Bold & Underline
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text, flags=re.DOTALL)

    # 8. Italic (*text* or _text_)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)

    # 9. Strikethrough (~~text~~)
    text = re.sub(r"~~([^~\n]+?)~~", r"<s>\1</s>", text)

    # 10. Links [title](url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', text)

    # 11. Clean horizontal rules (---)
    text = re.sub(r"\n\s*---\s*\n", "\n\n", text)

    # 12. Restore blockquotes
    for idx, snippet in enumerate(blockquotes):
        text = text.replace(f"@@@BLOCKQUOTE_{idx}@@@", snippet)

    # 13. Restore code blocks & inline code
    for idx, snippet in enumerate(inline_codes):
        text = text.replace(f"@@@INLINE_CODE_{idx}@@@", snippet)
    for idx, snippet in enumerate(code_blocks):
        text = text.replace(f"@@@CODE_BLOCK_{idx}@@@", snippet)

    # 14. Clean excessive spacing while preserving newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_telegram_html(text: str, max_chunk: int = 4000) -> list[str]:
    """Split formatted Telegram HTML text into chunks <= max_chunk characters,
    splitting on double newlines or single newlines to avoid tag tearing where possible.
    """
    if len(text) <= max_chunk:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > max_chunk:
        split_idx = remaining.rfind("\n\n", 0, max_chunk)
        if split_idx == -1:
            split_idx = remaining.rfind("\n", 0, max_chunk)
        if split_idx == -1:
            split_idx = remaining.rfind(" ", 0, max_chunk)
        if split_idx == -1:
            split_idx = max_chunk

        chunk = remaining[:split_idx].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_idx:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks
