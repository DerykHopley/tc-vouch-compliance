"""Helpers for reading an Assessment's steps in tests."""


def step_texts(result, node: str) -> list[str]:
    """The texts of the steps this node made, in order."""
    return [s["text"] for s in result.steps if s["node"] == node]


def in_order(nodes: list[str], *expected: str) -> bool:
    """Whether these nodes appear in this order among the steps' nodes, others between them
    allowed, so a node added to the graph doesn't break a test about the rest."""
    remaining = iter(nodes)
    return all(node in remaining for node in expected)
