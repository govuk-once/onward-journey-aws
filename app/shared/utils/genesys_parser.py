from typing import List, Dict, Any

def parse_genesys_blocks(blocks: List[Dict[str, Any]], is_inline: bool = False) -> str:
    """
    Recursively extracts text from Genesys Knowledge Base blocks.
    Preserves block-level spacing (newlines), inline spacing (punctuation),
    and prevents nested block collisions inside tables and lists.
    Ignores video and image blocks.
    """
    if not blocks:
        return ""

    text_parts = []
    for block in blocks:
        b_type = block.get("type")

        # 1. TEXT (Inline)
        if b_type == "Text" and "text" in block:
            content = block["text"].get("text", "")
            url = block["text"].get("hyperlink", "")

            # Preserve hyperlinks and shift any accidental spaces outside the Markdown brackets
            if url and content.strip():
                leading_space = content[:len(content) - len(content.lstrip())]
                trailing_space = content[len(content.rstrip()):]
                clean_text = content.strip()

                text_parts.append(f"{leading_space}[{clean_text}]({url}){trailing_space}")
            else:
                text_parts.append(content)

        # 2. PARAGRAPH (Block) - Safe to assume its direct children are inline text
        elif b_type == "Paragraph" and "paragraph" in block:
            container = block["paragraph"]
            if "blocks" in container:
                text_parts.append(parse_genesys_blocks(container["blocks"], is_inline=True))

        # 3. LISTS (Block)
        elif b_type in ["UnorderedList", "OrderedList"] and "list" in block:
            container = block["list"]
            if "blocks" in container:
                text_parts.append(parse_genesys_blocks(container["blocks"]))

        # 4. LIST ITEM (Block)
        elif b_type == "ListItem" and "blocks" in block:
            # Drop the is_inline=True here: List items can contain multiple paragraphs.
            # We parse them normally, but strip leading/trailing whitespace.
            item_text = parse_genesys_blocks(block["blocks"]).strip()
            if item_text:
                text_parts.append(f"- {item_text}")

        # 5. SECTION (Block)
        elif b_type == "Section" and "section" in block:
            container = block["section"]
            if "blocks" in container:
                text_parts.append(parse_genesys_blocks(container["blocks"]))

        # 6. TABLE (Block)
        elif b_type == "Table" and "table" in block:
            table_data = block["table"]
            table_string_parts = []

            # Handle Caption
            if "properties" in table_data and "caption" in table_data["properties"]:
                caption_blocks = table_data["properties"]["caption"].get("blocks", [])
                if caption_blocks:
                    table_string_parts.append(parse_genesys_blocks(caption_blocks, is_inline=True))

            # Handle Rows natively so they don't get broken by global double-newlines
            if "rows" in table_data:
                for row in table_data["rows"]:
                    row_text = []
                    for cell in row.get("cells", []):
                        if "blocks" in cell:
                            # Parse normally in case the cell contains paragraphs
                            cell_content = parse_genesys_blocks(cell["blocks"])
                            # Flatten multiple newlines/spaces into a single space to maintain Markdown row integrity
                            row_text.append(" ".join(cell_content.split()))
                        else:
                            row_text.append("")

                    # Join columns with a visual separator wrapped in pipes for strong Markdown table styling
                    table_string_parts.append(f"| {' | '.join(row_text)} |")

            # Append the completed table to the main document flow (joined by single newlines)
            if table_string_parts:
                text_parts.append("\n".join(table_string_parts))

    # Join logic: inline text merges seamlessly; blocks get double newlines
    if is_inline:
        return "".join(text_parts)
    else:
        return "\n\n".join(filter(None, [p.strip() for p in text_parts]))
