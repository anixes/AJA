import re

class MobileMDRenderer:
    """
    Converts standard Markdown into mobile-optimized formats for messaging platforms.
    Focuses on converting tables to bulleted lists for Telegram/WhatsApp readability.
    """

    # Matches GFM table delimiter row
    _TABLE_SEPARATOR_RE = re.compile(
        r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$'
    )

    @staticmethod
    def render(text: str) -> str:
        """Main entry point for mobile-friendly Markdown conversion."""
        if '|' not in text or '-' not in text:
            return text
        return MobileMDRenderer._wrap_markdown_tables(text)

    @staticmethod
    def _is_table_row(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and '|' in stripped

    @staticmethod
    def _split_row(line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    @staticmethod
    def _render_table_as_bullets(table_block: list[str]) -> str:
        if len(table_block) < 3:
            return "\n".join(table_block)

        headers = MobileMDRenderer._split_row(table_block[0])
        if len(headers) < 2:
            return "\n".join(table_block)

        rendered_rows: list[str] = []
        # Skip header and separator row
        for index, row in enumerate(table_block[2:], start=1):
            cells = MobileMDRenderer._split_row(row)
            # Ensure cells match headers length
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            
            heading = cells[0] if cells and cells[0] else f"Item {index}"
            
            rendered_rows.append(f"**{heading}**")
            for header, value in zip(headers, cells):
                if value and value != heading:
                    rendered_rows.append(f"• {header}: {value}")
        
        return "\n".join(rendered_rows)

    @staticmethod
    def _wrap_markdown_tables(text: str) -> str:
        lines = text.split('\n')
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Detect table: header row followed by separator row
            if (
                '|' in line
                and i + 1 < len(lines)
                and MobileMDRenderer._TABLE_SEPARATOR_RE.match(lines[i + 1])
            ):
                table_block = [line, lines[i + 1]]
                j = i + 2
                while j < len(lines) and MobileMDRenderer._is_table_row(lines[j]):
                    table_block.append(lines[j])
                    j += 1
                out.append(MobileMDRenderer._render_table_as_bullets(table_block))
                i = j
                continue

            out.append(line)
            i += 1

        return '\n'.join(out)
