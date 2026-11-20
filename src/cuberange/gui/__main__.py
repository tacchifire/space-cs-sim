"""Serve the local browser console."""
import argparse
from cuberange import ports
from .server import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=ports.gui())
    args = parser.parse_args()
    with make_server(host=args.host, port=args.port) as server:
        print(f"CubeRange GUI: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
