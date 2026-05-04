"""Allow `python -m svc.mcp_telekom_cc_selfcare` to run the service directly."""

from __future__ import annotations

import asyncio

from bin.run_service import run_service

if __name__ == "__main__":
    asyncio.run(run_service("mcp_telekom_cc_selfcare"))
