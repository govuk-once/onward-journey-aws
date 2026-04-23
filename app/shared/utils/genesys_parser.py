from typing import List, Dict, Any

def parse_genesys_blocks(blocks: List[Dict[str, Any]]) -> str:
    """
    Recursively flattens complex Genesys Knowledge Base blocks into a single string.
    Handles Paragraphs, Lists (Ordered/Unordered), ListItems, and Tables.
    Ignores Video and Image blocks.
    """
    text_parts = []
    if not blocks:
        return ""

    for block in blocks:
        b_type = block.get("type")

        # 1. TEXT: The leaf node containing the actual strings
        if b_type == "Text" and "text" in block:
            t_obj = block["text"]
            content = t_obj.get("text", "")
            url = t_obj.get("hyperlink", "")

            # Preserve hyperlinks in Markdown format for AI citations
            if url and content.strip():
                text_parts.append(f"[{content.strip()}]({url})")
            else:
                text_parts.append(content)

        # 2. PARAGRAPH: Container for more blocks
        elif b_type == "Paragraph" and "paragraph" in block:
            container = block["paragraph"]
            if "blocks" in container:
                text_parts.append(parse_genesys_blocks(container["blocks"]))

        # 3. LISTS: Container for ListItems
        elif b_type in ["UnorderedList", "OrderedList"] and "list" in block:
            container = block["list"]
            if "blocks" in container:
                text_parts.append(parse_genesys_blocks(container["blocks"]))

        # 4. LIST ITEM: Container for more blocks
        elif b_type == "ListItem" and "blocks" in block:
            text_parts.append(parse_genesys_blocks(block["blocks"]))

        # 5. SECTION: (Observed in some Genesys payloads)
        elif b_type == "Section" and "section" in block:
            container = block["section"]
            if "blocks" in container:
                text_parts.append(parse_genesys_blocks(container["blocks"]))

        # 6. TABLE: Complex nested structure
        elif b_type == "Table" and "table" in block:
            table_data = block["table"]
            # Handle Table Caption
            if "properties" in table_data and "caption" in table_data["properties"]:
                caption_blocks = table_data["properties"]["caption"].get("blocks", [])
                text_parts.append(parse_genesys_blocks(caption_blocks))

            # Handle Table Rows and Cells
            if "rows" in table_data:
                for row in table_data["rows"]:
                    for cell in row.get("cells", []):
                        if "blocks" in cell:
                            text_parts.append(parse_genesys_blocks(cell["blocks"]))

    # Filter out empty strings and whitespace-only parts, then join
    return " ".join(filter(None, [p.strip() for p in text_parts])).strip()

if __name__ == "__main__":
    # Small test suite to verify extraction logic
    test_json = [
        {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Hello World"}}]}},
        {"type": "UnorderedList", "list": {"blocks": [{"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "Point 1"}}]}]}},
        {"type": "Table", "table": {"rows": [{"cells": [{"blocks": [{"type": "Text", "text": {"text": "Cell Data"}}]}]}]}},
        {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Visit GOV.UK", "hyperlink": "https://gov.uk"}}]}}
    ]
    print(f"Test Extraction: {parse_genesys_blocks(test_json)}")
