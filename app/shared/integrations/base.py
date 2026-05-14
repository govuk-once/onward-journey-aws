import os
import json
from abc import ABC, abstractmethod
from typing import Dict, Any
from utils.aws import get_secrets_client

secrets_client = get_secrets_client()
ENV_PREFIX = os.environ.get("ENV_PREFIX")

class BaseCrmProvider(ABC):
    def __init__(self, identifier: str, config: Dict[str, Any]):
        """
        Initialises the CRM Provider with basic configuration and credentials.

        Args:
            identifier: The unique identifier for this CRM instance.
            config: Configuration dictionary from CRM_CONFIG_MAP.
        """
        self.identifier = identifier
        self.config = config
        self.creds = self._load_dynamic_secrets()
        self._token = None
        self._token_expiry = 0

    def _load_dynamic_secrets(self) -> Dict[str, Any]:
        """Retrieve and cache the raw secret data from AWS Secrets Manager."""
        full_secret_name = f"{ENV_PREFIX}/{self.config['secret_path']}"
        platform = self.config.get('platform', 'unknown').capitalize()
        print(f"Fetching {platform} Secrets from AWS Secrets Manager for: {self.identifier}")
        response = secrets_client.get_secret_value(SecretId=full_secret_name)
        return json.loads(response["SecretString"])

    @abstractmethod
    def fetch_adviser_availability(self) -> str: pass

    @abstractmethod
    def generate_handoff_signal(self, event: Dict[str, Any]) -> Dict[str, Any]: pass
