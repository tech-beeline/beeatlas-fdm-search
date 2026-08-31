from qdrant_client.models import PayloadSchemaType

from .base import BaseQdrantRepository
import logging

logger = logging.getLogger(__name__)


class BusinessCapabilityRepository(BaseQdrantRepository):

    def __init__(self):
        super().__init__(collection_name="business_capability")

    async def _ensure_payload_indexes(self):
        for field_name, schema in (
            ("internal_id", PayloadSchemaType.KEYWORD),
            ("code", PayloadSchemaType.KEYWORD),
            ("parent_codes", PayloadSchemaType.KEYWORD),
            ("is_domain", PayloadSchemaType.BOOL),
        ):
            try:
                await self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=schema,
                )
            except Exception as e:
                logger.debug("Payload index %s: %s", field_name, e)


bc_repository = BusinessCapabilityRepository()
