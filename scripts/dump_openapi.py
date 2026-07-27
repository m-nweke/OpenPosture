"""Write the API's OpenAPI schema to a file, without starting a server.

The frontend's types are generated from this document (OP-45), so it has to be produced the same
way in CI as it is locally — and starting uvicorn, polling a port and curling `/openapi.json`
introduces timing and networking into what is really a pure function of the app object.

`create_app()` builds the whole application without I/O, so the schema is available by asking it.

    uv run python scripts/dump_openapi.py apps/web/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openposture_api.config import Settings
from openposture_api.main import create_app


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <output.json>", file=sys.stderr)
        return 2

    # `load_backend=False` so dumping the schema never touches a model file. The schema is a
    # property of the routes, not of what is loaded behind them.
    app = create_app(
        Settings(environment="test", json_logs=True, pose_backend="fake"),
        load_backend=False,
    )

    destination = Path(argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Sorted keys and a trailing newline: the file is committed and diffed in CI, so its
    # serialisation must be stable across machines and Python versions. Without `sort_keys` the
    # ordering follows dict insertion, which is stable in practice but not guaranteed to survive
    # a FastAPI upgrade — and an unexplained reordering would look like a contract change.
    destination.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
