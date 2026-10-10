import argparse
import json
import logging
import os
from datetime import datetime

from signaldesk.db import connect, insert
from signaldesk.model import train
from signaldesk.monitor import loop, run_monitor
from signaldesk.seed import seed
from signaldesk.settings import stamp


def main():
    parser = argparse.ArgumentParser(description="SignalDesk operations")
    parser.add_argument("command", choices=["bootstrap", "train", "seed", "monitor", "worker"])
    parser.add_argument("--anchor", help="Fixed ISO UTC anchor for reproducible seed replay")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "bootstrap":
        print(json.dumps(seed(datetime.fromisoformat(args.anchor) if args.anchor else None)))
        print(json.dumps(run_monitor()))
    elif args.command == "train":
        train()
        with connect() as connection:
            if not connection.execute("SELECT 1 FROM deployments LIMIT 1").fetchone():
                insert(
                    connection,
                    "deployments",
                    {
                        "model_version": "router-v1",
                        "deployed_at": stamp(),
                        "reason": "Initial clean deployment",
                    },
                )
    elif args.command == "seed":
        print(json.dumps(seed(datetime.fromisoformat(args.anchor) if args.anchor else None)))
    elif args.command == "monitor":
        print(json.dumps(run_monitor()))
    else:
        loop(int(os.getenv("MONITOR_INTERVAL_SECONDS", "60")))


if __name__ == "__main__":
    main()
