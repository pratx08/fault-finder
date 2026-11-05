"""Small seeded target for live-agent integration evaluation."""


class Payload:
    def __init__(self):
        self.data = bytearray(1024)


class Worker:
    def __init__(self):
        self.listeners = []

    def process(self, subscribe=True):
        payload = Payload()
        if subscribe:
            self.listeners.append(lambda: payload)
        return payload

    def close(self):
        self.listeners.clear()
