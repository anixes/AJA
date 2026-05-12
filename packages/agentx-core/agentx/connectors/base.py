import logging
from typing import Dict, List, Any, Optional, Type
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)

class Document:
    """Standardized document format for all connectors."""
    def __init__(
        self,
        doc_id: str,
        source: str,
        content: str,
        title: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.doc_id = doc_id
        self.source = source
        self.content = content
        self.title = title
        self.timestamp = timestamp or datetime.utcnow()
        self.metadata = metadata or {}

class BaseConnector(ABC):
    """Base class for all AgentX connectors."""
    
    connector_id: str = "base"
    display_name: str = "Base Connector"
    auth_type: str = "none" # none, local, oauth, apikey

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the connector is functional and authenticated."""
        pass

    @abstractmethod
    def sync(self, since: Optional[datetime] = None) -> List[Document]:
        """Fetch data from the source and return a list of Documents."""
        pass

    def get_tools(self) -> List[Dict[str, Any]]:
        """Return a list of tool definitions provided by this connector."""
        return []

class ConnectorRegistry:
    """Central registry for all app connectors."""
    _connectors: Dict[str, Type[BaseConnector]] = {}
    _instances: Dict[str, BaseConnector] = {}

    @classmethod
    def register(cls, connector_id: str):
        def wrapper(connector_cls: Type[BaseConnector]):
            cls._connectors[connector_id] = connector_cls
            return connector_cls
        return wrapper

    @classmethod
    def get_connector(cls, connector_id: str) -> Optional[BaseConnector]:
        if connector_id in cls._instances:
            return cls._instances[connector_id]
        
        if connector_id in cls._connectors:
            instance = cls._connectors[connector_id]()
            cls._instances[connector_id] = instance
            return instance
        return None

    @classmethod
    def list_connectors(cls) -> List[Dict[str, str]]:
        return [
            {"id": cid, "name": ccls.display_name, "auth": ccls.auth_type}
            for cid, ccls in cls._connectors.items()
        ]
