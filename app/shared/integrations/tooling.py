import json
from typing import Dict, Any, List

def format_jsonrpc_response(request_id: Any, result: Any) -> Dict[str, Any]:
    """Wraps a tool result in JSON-RPC 2.0 format."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }

def format_tool_text_result(text: str) -> Dict[str, Any]:
    """Helper to format a simple text result for MCP tools."""
    return {"content": [{"type": "text", "text": text}]}

def log_metric(metric_name: str, properties: Dict[str, Any]):
    """Standardised metric logging for CloudWatch logs."""
    props_str = " | ".join([f"{k}: {v}" for k, v in properties.items()])
    print(f"METRIC | {metric_name} | {props_str}")
