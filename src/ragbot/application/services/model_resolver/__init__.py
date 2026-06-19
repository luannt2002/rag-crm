"""DB-driven AI strategy resolver — package shim (re-export).

Strangler split: service.py (class) + _cache_mixin + _binding_mixin + _helpers.
Import path ``model_resolver import ModelResolverService`` GIỮ NGUYÊN.
"""
from ragbot.application.services.model_resolver.service import ModelResolverService  # noqa: F401
from ragbot.application.services.model_resolver._helpers import *  # noqa: F401,F403
