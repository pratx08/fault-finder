from app import process_request


def test_request_returns_expected_size():
    assert process_request() == 65536
