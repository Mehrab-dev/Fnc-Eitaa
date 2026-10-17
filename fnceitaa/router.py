from typing import Callable, Any, Dict, List, Union, Awaitable
import inspect

Handler = Callable[..., Union[Awaitable[Any], Any]]


class Router:
    """Handles event routing and handler registrations."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = {}

    def on(self, event_name: str):
        """Decorator to register a handler for a specific event."""
        def decorator(func: Handler) -> Handler:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
            self._handlers[event_name].append(func)
            return func
        return decorator

    def on_message(self):
        """Shortcut decorator for new message events."""
        return self.on("message")

    async def emit(self, event_name: str, payload: Any) -> None:
        """Triggers all handlers registered for the given event."""
        handlers = self._handlers.get(event_name, [])
        first_error = None
        for handler in handlers:
            try:
                res = handler(payload)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
