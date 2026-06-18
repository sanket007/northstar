from __future__ import annotations
import argparse
from pathlib import Path

from orchestrator.config import load_config
from orchestrator.plane import PlaneClient
from orchestrator import poller


def main() -> None:
    ap = argparse.ArgumentParser(prog="orchestrator")
    ap.add_argument("--config", default="config.yaml", type=Path)
    ap.add_argument("--print-states", action="store_true",
                    help="print Plane state name→id map and exit")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.print_states:
        client = PlaneClient(cfg.plane_base_url, cfg.plane_api_key,
                             cfg.plane_workspace_slug, cfg.plane_project_id)
        for name, sid in client.list_states().items():
            print(f"{name}: {sid}")
        return
    poller.run(cfg)


if __name__ == "__main__":
    main()
