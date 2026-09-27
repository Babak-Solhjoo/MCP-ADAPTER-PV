"""Software adapters. Each module exposes an adapter class with high-level, headless actions."""
from __future__ import annotations

from .base import BaseAdapter, RunResult

__all__ = ["BaseAdapter", "RunResult"]
