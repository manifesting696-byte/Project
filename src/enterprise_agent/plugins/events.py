"""Ordered synchronous hooks with explicit, reversible subscriptions."""

import logging
from collections import defaultdict
from threading import RLock


class Events:
    def __init__(self):
        self._listeners = defaultdict(list)
        self._lock = RLock()

    def on(self, name, handler, priority=0):
        registration = (priority, object(), handler)
        with self._lock:
            self._listeners[name].append(registration)
            self._listeners[name].sort(key=lambda item: item[0])

        def dispose():
            with self._lock:
                if registration in self._listeners[name]:
                    self._listeners[name].remove(registration)
                if not self._listeners[name]:
                    self._listeners.pop(name, None)

        return dispose

    def emit(self, name, payload):
        with self._lock:
            listeners = tuple(self._listeners.get(name, ()))
        for _, _, handler in listeners:
            handler(payload)

    def notify(self, name, payload):
        """Lifecycle and telemetry observers cannot undo an already committed action."""
        with self._lock:
            listeners = tuple(self._listeners.get(name, ()))
        for _, _, handler in listeners:
            try:
                handler(payload)
            except Exception:
                logging.getLogger(__name__).exception("Event observer failed: %s", name)

    def counts(self):
        with self._lock:
            return {name: len(items) for name, items in self._listeners.items()}
