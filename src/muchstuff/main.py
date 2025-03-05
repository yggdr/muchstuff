import argparse
import os
import sys

from textual.app import App

from .tui import ReposApp


def parse_args():
    parser = argparse.ArgumentParser()
    # parser.add_argument('-v', '--verbose', help="More verbose output; can be given multiple times", action='count', default=0)
    parser.add_argument('-d', '--debug', help='Run in debug mode (debug-dependencies are needed for this!)', action='store_true')
    parser.add_argument('-c', '--config', help='Use alternative config file', default=None)
    parser.add_argument('-V', '--version', help='Print version and exit', action='store_true')
    return parser.parse_args(namespace=argparse.Namespace(prog=parser.prog))


def _run(app: App, debug: bool):
    if debug:
        return app._debug_run()
    else:
        return app.run()


def main(args: argparse.Namespace | None = None) -> int:
    if args is None:
        args = parse_args()

    if args.version:
        from . import __version__
        print(args.prog, __version__)
        sys.exit(os.EX_OK)

    app = ReposApp(args.config)
    result = _run(app, args.debug)

    if result is not None:
        print(result)

    return app.return_code


if __name__ == "__main__":
    sys.exit(main())
