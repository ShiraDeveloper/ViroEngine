"""Pipeline stages S0-S11 (PLAN §4). Each stage is a function whose dependencies are
injected as keyword-only ports; stages communicate through models only."""

from __future__ import annotations