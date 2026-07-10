from langbridge_code.ui.message_queue import UserMessageQueue


def test_enqueue_dequeue_fifo():
    queue = UserMessageQueue(max_size=3)
    assert queue.enqueue("first")
    assert queue.enqueue("second")
    assert len(queue) == 2
    assert queue.dequeue() == "first"
    assert queue.dequeue() == "second"
    assert queue.dequeue() is None


def test_enqueue_rejects_empty_and_when_full():
    queue = UserMessageQueue(max_size=2)
    assert not queue.enqueue("")
    assert queue.enqueue("one")
    assert queue.enqueue("two")
    assert not queue.enqueue("three")
    assert queue.full


def test_clear_returns_count():
    queue = UserMessageQueue()
    queue.enqueue("a")
    queue.enqueue("b")
    assert queue.clear() == 2
    assert len(queue) == 0


def test_items_snapshot():
    queue = UserMessageQueue()
    queue.enqueue("alpha")
    queue.enqueue("beta")
    assert queue.items() == ["alpha", "beta"]
    queue.dequeue()
    assert queue.items() == ["beta"]
