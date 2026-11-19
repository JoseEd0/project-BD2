"""El API REST del servicio de rostros: registro con consentimiento y controles de calidad,
reconocimiento y borrado. Habla con el gestor solo por `minigestor_client`."""

from faces.api.app import create_app

__all__ = ["create_app"]
