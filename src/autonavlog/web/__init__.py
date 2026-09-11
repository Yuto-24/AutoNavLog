"""Web entry points, loaded only when the server is requested."""

from typing import Any

__all__ = ["WebRuntimeConfig", "build_web_application", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    if name in {"WebRuntimeConfig", "build_web_application"}:
        from .runtime import WebRuntimeConfig, build_web_application

        return {
            "WebRuntimeConfig": WebRuntimeConfig,
            "build_web_application": build_web_application,
        }[name]
    raise AttributeError(name)
