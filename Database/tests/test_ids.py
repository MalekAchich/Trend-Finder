import time
import uuid

from tf_db.ids import uuid7


def test_uuid7_version_and_variant():
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_sorts_by_creation_time():
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    assert a < b


def test_uuid7_is_unique():
    assert len({uuid7() for _ in range(2000)}) == 2000
