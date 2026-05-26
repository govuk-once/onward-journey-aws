import unittest
from typing import List, Dict, Any
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shared.utils.genesys_parser import parse_genesys_blocks
class TestGenesysParser(unittest.TestCase):

    def test_01_inline_punctuation_spacing(self):
        """Tests that inline text nodes don't get phantom spaces around punctuation."""
        payload = [{
            "type": "Paragraph",
            "paragraph": {
                "blocks": [
                    {"type": "Text", "text": {"text": "Hello"}},
                    {"type": "Text", "text": {"text": ","}},
                    {"type": "Text", "text": {"text": " world"}},
                    {"type": "Text", "text": {"text": "."}}
                ]
            }
        }]
        expected = "Hello, world."
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_02_hyperlink_formatting(self):
        """Tests that hyperlinks are converted to Markdown format correctly."""
        payload = [{
            "type": "Paragraph",
            "paragraph": {
                "blocks": [
                    {"type": "Text", "text": {"text": "Click "}},
                    {"type": "Text", "text": {"text": "here", "hyperlink": "https://example.com"}},
                    {"type": "Text", "text": {"text": " to learn more."}}
                ]
            }
        }]
        expected = "Click [here](https://example.com) to learn more."
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_03_nested_blocks_in_list_items(self):
        """Tests the 'gotcha': a list item containing multiple paragraphs."""
        payload = [{
            "type": "UnorderedList",
            "list": {
                "blocks": [{
                    "type": "ListItem",
                    "blocks": [
                        {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Paragraph One"}}]}},
                        {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Paragraph Two"}}]}}
                    ]
                }]
            }
        }]
        # Should preserve the newline between the paragraphs inside the bullet
        expected = "- Paragraph One\n\nParagraph Two"
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_04_table_flattening_and_caption(self):
        """Tests that multi-paragraph table cells are flattened and tables look like Markdown."""
        payload = [{
            "type": "Table",
            "table": {
                "properties": {
                    "caption": {"blocks": [{"type": "Text", "text": {"text": "Financial Data"}}]}
                },
                "rows": [{
                    "cells": [
                        {
                            "blocks": [
                                {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Revenue"}}]}},
                                {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "(Q1)"}}]}}
                            ]
                        },
                        {
                            "blocks": [{"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "$10,000"}}]}}]
                        }
                    ]
                }]
            }
        }]
        # Caption gets its own line, row gets flattened and wrapped in pipes
        expected = "Financial Data\n| Revenue (Q1) | $10,000 |"
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_05_empty_and_missing_fields(self):
        """Tests safety against KeyErrors when text objects are empty or missing."""
        payload = [
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {}}]}}, # Missing "text" key
            {"type": "Paragraph"}, # Missing "paragraph" key
            {"type": "Text"} # Missing "text" key entirely
        ]
        expected = ""
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_06_ignored_blocks(self):
        """Tests that Image and Video blocks are safely skipped without crashing."""
        payload = [
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Before image."}}]}},
            {"type": "Image", "image": {"url": "https://example.com/img.png"}},
            {"type": "Video", "video": {"url": "https://example.com/vid.mp4"}},
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "After video."}}]}}
        ]
        expected = "Before image.\n\nAfter video."
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_07_section_handling(self):
        """Tests that section wrappers correctly pass through their contents."""
        payload = [{
            "type": "Section",
            "section": {
                "blocks": [{"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Section content."}}]}}]
            }
        }]
        expected = "Section content."
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_08_complex_mixed_document(self):
        """Tests a realistic document structure: Paragraph -> Long List -> Table -> Paragraph."""
        payload = [
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Follow these steps:"}}]}},
            {
                "type": "OrderedList",
                "list": {
                    "blocks": [
                        {"type": "ListItem", "blocks": [{"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Open the app."}}]}}]},
                        {"type": "ListItem", "blocks": [{"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Click "}}, {"type": "Text", "text": {"text": "Settings", "hyperlink": "https://example.com"}}]}}]},
                        {"type": "ListItem", "blocks": [{"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Save your changes."}}]}}]}
                    ]
                }
            },
            {
                "type": "Table",
                "table": {
                    "rows": [{
                        "cells": [
                            {"blocks": [{"type": "Text", "text": {"text": "Status"}}]},
                            {"blocks": [{"type": "Text", "text": {"text": "Complete"}}]}
                        ]
                    }]
                }
            },
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "Done."}}]}}
        ]

        expected = (
            "Follow these steps:\n\n"
            "- Open the app.\n\n"
            "- Click [Settings](https://example.com)\n\n"
            "- Save your changes.\n\n"
            "| Status | Complete |\n\n"
            "Done."
        )
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_09_rich_text_inside_table_cells(self):
        """Tests that lists and hyperlinks inside a table cell are flattened gracefully for Markdown."""
        payload = [{
            "type": "Table",
            "table": {
                "rows": [{
                    "cells": [
                        {"blocks": [{"type": "Text", "text": {"text": "Features"}}]},
                        {
                            "blocks": [{
                                "type": "UnorderedList",
                                "list": {
                                    "blocks": [
                                        {"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "Fast"}}]},
                                        {"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "Secure ", "hyperlink": "https://sec.com"}}]}
                                    ]
                                }
                            }]
                        }
                    ]
                }]
            }
        }]
        # The list inside the cell should be parsed as "- Fast \n\n - [Secure](https://sec.com)"
        # Then, the cell flattener will collapse it to "- Fast - [Secure](https://sec.com)"
        expected = "| Features | - Fast - [Secure](https://sec.com) |"
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_10_deeply_nested_sections(self):
        """Tests extreme nesting (Section -> Section -> List -> Paragraph -> Text) without losing data."""
        payload = [{
            "type": "Section",
            "section": {
                "blocks": [{
                    "type": "Section",
                    "section": {
                        "blocks": [{
                            "type": "UnorderedList",
                            "list": {
                                "blocks": [{
                                    "type": "ListItem",
                                    "blocks": [{
                                        "type": "Paragraph",
                                        "paragraph": {
                                            "blocks": [{"type": "Text", "text": {"text": "Deeply nested text."}}]
                                        }
                                    }]
                                }]
                            }
                        }]
                    }
                }]
            }
        }]
        expected = "- Deeply nested text."
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_11_simple_paragraphs(self):
        """Tests that standard paragraphs are extracted and separated by double newlines."""
        payload = [
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "This is the first paragraph."}}]}},
            {"type": "Paragraph", "paragraph": {"blocks": [{"type": "Text", "text": {"text": "This is the second paragraph."}}]}}
        ]
        expected = "This is the first paragraph.\n\nThis is the second paragraph."
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_12_basic_unordered_list(self):
        """Tests a standard bulleted list with simple text items."""
        payload = [{
            "type": "UnorderedList",
            "list": {
                "blocks": [
                    {"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "First item"}}]},
                    {"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "Second item"}}]}
                ]
            }
        }]
        expected = "- First item\n\n- Second item"
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_13_basic_ordered_list(self):
        """Tests a standard numbered/ordered list. (Note: parser standardises all list items to markdown hyphens)."""
        payload = [{
            "type": "OrderedList",
            "list": {
                "blocks": [
                    {"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "Step one"}}]},
                    {"type": "ListItem", "blocks": [{"type": "Text", "text": {"text": "Step two"}}]}
                ]
            }
        }]
        # The parser currently outputs "- " for all list items, which is valid Markdown for LLMs
        expected = "- Step one\n\n- Step two"
        self.assertEqual(parse_genesys_blocks(payload), expected)

    def test_14_simple_table(self):
        """Tests a clean, basic 2x2 table without any nested complexities or captions."""
        payload = [{
            "type": "Table",
            "table": {
                "rows": [
                    {
                        "cells": [
                            {"blocks": [{"type": "Text", "text": {"text": "Header A"}}]},
                            {"blocks": [{"type": "Text", "text": {"text": "Header B"}}]}
                        ]
                    },
                    {
                        "cells": [
                            {"blocks": [{"type": "Text", "text": {"text": "Data A"}}]},
                            {"blocks": [{"type": "Text", "text": {"text": "Data B"}}]}
                        ]
                    }
                ]
            }
        }]
        expected = "| Header A | Header B |\n| Data A | Data B |"
        self.assertEqual(parse_genesys_blocks(payload), expected)

if __name__ == "__main__":
    unittest.main()
