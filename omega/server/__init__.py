"""D1 daemon: a FastAPI + uvicorn server exposing omega's agent loop over
REST/WebSocket for the Omega.app SwiftUI client -- see Phase 9 of the plan.
`omega serve` (wired up separately in omega/__main__.py) and
`python -m omega.server` both call `main()`."""
from .app import create_app, main

__all__ = ["create_app", "main"]
