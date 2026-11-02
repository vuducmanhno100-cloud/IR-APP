import argparse
import inspect
import json
import sys

from ..routers import system, tv1, tv2
from ..testing.contract import operations


def operation_status() -> list[dict]:
    by_route = {(op["method"], op["path"]): (op_id, op) for op_id, op in operations().items()}
    rows = []
    for router in (system.router, tv1.router, tv2.router):
        for route in router.routes:
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                op_id, op = by_route[(method, route.path)]
                stub = "not_implemented(" in inspect.getsource(route.endpoint)
                rows.append({"owner": op["x-owner"], "operation": op_id, "method": method, "path": route.path, "done": not stub})
    return sorted(rows, key=lambda r: (r["owner"], r["path"], r["method"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ir_engine.tools.stub_report", description="Endpoint nào còn trả 501")
    parser.add_argument("--owner", choices=["TV1", "TV2"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 nếu còn endpoint chưa làm")
    args = parser.parse_args(argv)
    rows = [r for r in operation_status() if not args.owner or r["owner"] == args.owner]
    pending = [r for r in rows if not r["done"]]
    if args.json:
        print(json.dumps({"total": len(rows), "pending": len(pending), "operations": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['owner']}  {'done' if r['done'] else '501 '}  {r['method']:<6} {r['path']:<34} {r['operation']}")
        print(f"\n{len(rows) - len(pending)}/{len(rows)} done, {len(pending)} pending")
    return 1 if args.strict and pending else 0


if __name__ == "__main__":
    sys.exit(main())
