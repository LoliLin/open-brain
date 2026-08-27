from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAI-compatible human-in-the-loop server")
    parser.add_argument("--host", default=os.environ.get("OPEN_BRAIN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPEN_BRAIN_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    print(f"Operator console: http://127.0.0.1:{args.port}/")
    print(f"OpenAI base URL:  http://127.0.0.1:{args.port}/v1")
    uvicorn.run("open_brain.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
