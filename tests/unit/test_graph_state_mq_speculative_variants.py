"""M17 regression — ``_mq_speculative_variants`` must be a declared GraphState field.

The query graph writes ``merged["_mq_speculative_variants"] = mq_variants``
(query_graph.py) and the retrieve node reads
``state.get("_mq_speculative_variants")`` (retrieve.py). LangGraph drops any
state key absent from the TypedDict schema during the reducer merge across node
hops, so an undeclared key silently vanishes and the speculative paraphrase set
is discarded — re-running the inline LLM expansion that was already paid for.

The field must mirror its sibling ``_mq_queries`` (both carry a list of
paraphrase strings handed off between nodes), so its annotation must be
identical — same reducer / overwrite semantics.
"""

from ragbot.orchestration.state import GraphState


class TestMqSpeculativeVariantsDeclared:
    def test_field_is_declared_on_graph_state(self):
        assert "_mq_speculative_variants" in GraphState.__annotations__

    def test_annotation_matches_sibling_mq_queries(self):
        annotations = GraphState.__annotations__
        # Sibling hand-off slot — must already exist.
        assert "_mq_queries" in annotations
        # Same reducer/annotation type: both are paraphrase-string lists
        # handed off between nodes, so they must merge identically.
        assert (
            annotations["_mq_speculative_variants"]
            == annotations["_mq_queries"]
        )
