from enum import Enum, auto
from utils.config import CRM_CONFIG_MAP
from integrations.base import BaseCrmProvider
from integrations.providers.genesys import GenesysProvider

class Capability(Enum):
    CHAT_AVAILABILITY = auto()
    KB_SYNC_META = auto()
    KB_FETCH = auto()

class ProviderFactory:
    @staticmethod
    def get_provider(identifier: str, capability: Capability) -> BaseCrmProvider:
        """
        Factory method to get an initialized provider instance for a specific capability.
        """
        config = CRM_CONFIG_MAP.get(identifier)
        if not config:
            raise ValueError(f"No configuration found for identifier: {identifier}")

        platform = config.get("platform")

        if platform == "genesys":
            return GenesysProvider(identifier, config)
        # Additional platforms added here
        raise ValueError(f"Unsupported platform '{platform}' for identifier: {identifier}")
