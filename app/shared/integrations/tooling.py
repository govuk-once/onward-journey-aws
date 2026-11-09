import logging
import os
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def log_metric(metric_name: str, properties: Dict[str, Any]):
    """Standardised metric logging for CloudWatch logs."""
    props_str = " | ".join([f"{k}: {v}" for k, v in properties.items()])
    logger.info(f"METRIC | {metric_name} | {props_str}")
