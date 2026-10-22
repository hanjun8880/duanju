from src.domains.gateway.adapters import flux, minimax, seedance, wan
from src.domains.gateway.adapters.base import HttpAdapter

# keyed by model_catalog.provider
ADAPTERS: dict[str, HttpAdapter] = {c.name: HttpAdapter(c) for c in
                                    (seedance.CONFIG, wan.CONFIG, minimax.CONFIG, flux.CONFIG)}
