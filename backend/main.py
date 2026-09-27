"""Application entry point."""

import logging

from backend.server import serve


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    serve()


if __name__ == "__main__":
    main()
