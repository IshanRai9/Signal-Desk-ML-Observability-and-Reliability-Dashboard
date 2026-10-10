"""Submit JSONL tickets in bounded batches; output event IDs for delayed labels."""

import argparse
import json
import os
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size must be 1..1000")
    with (
        httpx.Client(timeout=120, headers={"X-API-Key": os.getenv("API_KEY", "")}) as client,
        args.file.open(encoding="utf-8") as stream,
    ):
        buffer = []
        for line in stream:
            if line.strip():
                buffer.append(json.loads(line))
            if len(buffer) == args.batch_size:
                response = client.post(f"{args.api}/v1/batch", json={"tickets": buffer})
                response.raise_for_status()
                print(json.dumps(response.json()))
                buffer = []
        if buffer:
            response = client.post(f"{args.api}/v1/batch", json={"tickets": buffer})
            response.raise_for_status()
            print(json.dumps(response.json()))


if __name__ == "__main__":
    main()
