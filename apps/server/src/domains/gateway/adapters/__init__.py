from typing import Any

from src.domains.gateway.adapters import flux, keling, minimax, seedance, wan
from src.domains.gateway.adapters.ark import ArkAdapter
from src.domains.gateway.adapters.base import HttpAdapter

# keyed by model_catalog.provider. ark is the vendor checked against official docs; the others are
# reseller-style placeholders kept configurable. keling has no verified docs/key yet (NOT_TESTED, inactive).
ADAPTERS: dict[str, Any] = {c.name: HttpAdapter(c) for c in
                           (seedance.CONFIG, wan.CONFIG, minimax.CONFIG, flux.CONFIG, keling.CONFIG)}
ADAPTERS["ark"] = ArkAdapter()
