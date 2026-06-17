"""Notify infrastructure — webhook dispatcher + future channels.

Each transport adapter under this package implements its own
side-effect-only fire-and-forget send semantics. Callers schedule
dispatch through ``asyncio.create_task`` so the alert path never
blocks the business logic that surfaced the error.
"""
