import logging

from collectors.base import BaseCollector
from models.event import Event

logger = logging.getLogger(__name__)

# E-REDES (formerly EDP Distribuição) does not expose a public REST API for
# live power outages. The OpenDataSoft portal at e-redes.opendatasoft.com
# currently has no outage dataset. This collector is a placeholder — implement
# when a live feed becomes available.
#
# Dataset catalog: https://e-redes.opendatasoft.com/api/explore/v2.1/catalog/datasets


class EdpCollector(BaseCollector):
    source_name = "edp"

    async def fetch(self) -> list[Event]:
        logger.debug("EdpCollector: no live data source available — skipping")
        return []
