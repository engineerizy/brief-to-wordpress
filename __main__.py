import argparse

import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the Brief to WordPress console locally.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
