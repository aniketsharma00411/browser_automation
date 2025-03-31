

class IdManager:
    """Manages unique IDs for CDP commands"""

    def __init__(self):
        self._current_id = 0

    def next_id(self) -> int:
        """Get the next available ID"""
        self._current_id += 1
        return self._current_id

    def get_ids(self, count: int) -> list:
        """Get multiple sequential IDs"""
        ids = [self.next_id() for _ in range(count)]
        return ids
