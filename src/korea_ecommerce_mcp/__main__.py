from __future__ import annotations

import os

from korea_ecommerce_mcp.server import mcp


def main() -> None:
    transport = os.getenv("KEIC_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http"}:
        raise SystemExit("KEIC_TRANSPORT must be 'stdio' or 'streamable-http'")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
