"""tradingview_ideas.py — THE single entry-point for the TradingView Ideas pipeline.

Scrape community trading ideas → (manual chart read by Claude Code) → execute the
chart-read brackets onto the demo/test venues, and resolve them against real price
data. All implementation lives in the `ideas/` package; this is the one file in the
project root you run to drive the whole thing.

Commands
--------
  python tradingview_ideas.py scrape [--limit N]   pull + store new ideas (default 10)
  python tradingview_ideas.py vision               list ideas awaiting a chart read (JSON)
  python tradingview_ideas.py set ID --tf 4h --direction -1 \
        --entry E --target T --stop S [--basis chart] [--confidence C]
                                                   write a chart-read bracket back to idea ID
  python tradingview_ideas.py run                  place orders + fill/resolve on test venues
  python tradingview_ideas.py show                 print the ideas table
  python tradingview_ideas.py all [--limit N]      scrape, then run, in one go

Daily use (see tradingview_automation_run.md): `scrape` → read the charts the
`vision` list points to → `set` each → `run`. `run` is also called every cron
cycle by daily.py, so resting orders fill + resolve unattended.

Floors: paper/demo only; ≤20 concurrent open trades; timeframe-agnostic. Real
money still requires LIVE_BUDGET_ARMED (off).
"""
import argparse
import sys

from ideas import scrape, execute


def _dispatch(module_main, argv):
    """Run a sub-module's argparse main() with a synthesized argv (reuses the
    existing, tested CLIs in ideas.scrape / ideas.execute without duplicating them)."""
    old = sys.argv
    sys.argv = [module_main.__module__] + argv
    try:
        module_main()
    finally:
        sys.argv = old


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scrape = sub.add_parser("scrape", help="pull + store new ideas")
    p_scrape.add_argument("--limit", type=int, default=10)
    p_scrape.add_argument("--probe", action="store_true")

    sub.add_parser("vision", help="list ideas awaiting a chart read (JSON)")
    sub.add_parser("show", help="print the ideas table")

    p_set = sub.add_parser("set", help="write a chart-read bracket to idea ID")
    p_set.add_argument("id", type=int)
    p_set.add_argument("--tf", type=str)
    p_set.add_argument("--direction", type=int, choices=[-1, 0, 1])
    p_set.add_argument("--entry", type=float)
    p_set.add_argument("--target", type=float)
    p_set.add_argument("--stop", type=float)
    p_set.add_argument("--basis", type=str, default="chart")
    p_set.add_argument("--confidence", type=float)

    p_run = sub.add_parser("run", help="place orders + fill/resolve on test venues")
    p_run.add_argument("--probe", action="store_true")
    p_run.add_argument("--open", action="store_true", help="only place resting orders")
    p_run.add_argument("--resolve", action="store_true", help="only fill + resolve")

    p_all = sub.add_parser("all", help="scrape then run")
    p_all.add_argument("--limit", type=int, default=10)

    args = ap.parse_args()

    if args.cmd == "scrape":
        a = ["--limit", str(args.limit)] + (["--probe"] if args.probe else [])
        _dispatch(scrape.main, a)
    elif args.cmd == "vision":
        _dispatch(scrape.main, ["--list-vision"])
    elif args.cmd == "show":
        _dispatch(scrape.main, ["--show"])
    elif args.cmd == "set":
        a = ["--set-levels", str(args.id), "--basis", args.basis]
        for flag, val in (("--tf", args.tf), ("--direction", args.direction),
                          ("--entry", args.entry), ("--target", args.target),
                          ("--stop", args.stop), ("--confidence", args.confidence)):
            if val is not None:
                a += [flag, str(val)]
        _dispatch(scrape.main, a)
    elif args.cmd == "run":
        a = (["--probe"] if args.probe else []) + (["--open"] if args.open else []) \
            + (["--resolve"] if args.resolve else [])
        _dispatch(execute.main, a)
    elif args.cmd == "all":
        print("=== scrape ===")
        _dispatch(scrape.main, ["--limit", str(args.limit)])
        print("\n=== run (place + resolve on test venues) ===")
        _dispatch(execute.main, [])


if __name__ == "__main__":
    main()
