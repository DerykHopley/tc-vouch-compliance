"""Helpers for reading an Assessment's steps in tests."""


def step_texts(result, node: str) -> list[str]:
    """The texts of the steps this node made, in order."""
    return [s["text"] for s in result.steps if s["node"] == node]
