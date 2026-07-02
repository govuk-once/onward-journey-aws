from typing import Dict, Any

def log_metric(metric_name: str, properties: Dict[str, Any]):
    """Standardised metric logging for CloudWatch logs."""
    props_str = " | ".join([f"{k}: {v}" for k, v in properties.items()])
    print(f"METRIC | {metric_name} | {props_str}")
