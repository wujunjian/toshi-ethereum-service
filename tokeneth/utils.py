
class RedisLockException(Exception):
    pass

class RedisLock:
    def __init__(self, redis, key, raise_when_locked=None, prefix="lock:", ex=30):
        self.redis = redis
        self.key = prefix + key
        self.raise_when_locked = raise_when_locked or RedisLockException
        self.ex = ex
        self.locked = None

    def __enter__(self):
        self.locked = locked = self.redis.set(self.key, True, nx=True, ex=self.ex)
        if not locked:
            raise self.raise_when_locked()

    def __exit__(self, exc_type, exc_value, traceback):
        if self.locked:
            self.redis.delete(self.key)
