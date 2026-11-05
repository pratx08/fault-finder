"""Intentionally faulty application used to demonstrate source-based test detection."""

completed_results: list[bytearray] = []


def process_request() -> int:
    result = bytearray(65536)
    completed_results.append(result)
    return len(result)
