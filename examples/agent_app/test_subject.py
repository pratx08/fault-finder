from subject import Worker


def test_operation():
    worker = Worker()
    payload = worker.process()
    assert len(payload.data) == 1024
    worker.close()
