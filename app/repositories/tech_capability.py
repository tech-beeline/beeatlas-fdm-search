from .base import BaseQdrantRepository

class TechCapabilityRepository(BaseQdrantRepository):
    """Репозиторий для работы с tech_capability"""

    def __init__(self):
        super().__init__(collection_name="tech_capability")

tc_repository = TechCapabilityRepository()