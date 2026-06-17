"""Safety strategy adapters — anti-poisoning + content gating.

Houses Strategy implementations behind the safety-related ports:

- :mod:`ragbot.application.ports.source_validator_port` — per-bot source
  URL allow-list (PoisonedRAG defence).

Each adapter is registered in :mod:`ragbot.infrastructure.safety.registry`
and selected at runtime via the matching ``system_config`` provider key.
Default OFF (Null adapter) so existing tenants see no behaviour change
until the bot owner opts in.
"""
