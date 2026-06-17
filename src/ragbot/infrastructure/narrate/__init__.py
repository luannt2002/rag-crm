"""Narrate-then-Embed strategy package.

Default OFF. Operator opts in via ``system_config.narrate_then_embed_enabled``;
the DI container then wires ``LLMNarrateGenerator`` in place of
``NullNarrateGenerator``.
"""
