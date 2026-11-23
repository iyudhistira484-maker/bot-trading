"""Create a local password or serve the localhost-only paper dashboard."""
import argparse
import getpass
from pathlib import Path
import sys

from src.dashboard.auth import Auth, write_password
from src.dashboard.server import DashboardServer
from src.dashboard.store import PaperStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'serve'])
    parser.add_argument('--config', default='config/config.yaml')
    parser.add_argument('--auth-file', default='data/dashboard-auth.json')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    path = Path(args.auth_file)
    if args.action == 'setup':
        if not sys.stdin.isatty():
            print('Setup requires an interactive terminal.', file=sys.stderr); return 1
        try:
            password = getpass.getpass('Create dashboard password (at least 16 characters): ')
            repeated = getpass.getpass('Repeat dashboard password: ')
            if password != repeated:
                print('Passwords do not match.', file=sys.stderr); return 1
            write_password(path, password)
            print('Dashboard password saved securely; no password was printed.')
            return 0
        except (FileExistsError, ValueError, OSError):
            print('Password setup failed: existing file, invalid password, or filesystem error.', file=sys.stderr); return 1
    try:
        server = DashboardServer(args.port, Auth(path), PaperStore(args.config))
        print(f'Paper dashboard listening on 127.0.0.1:{args.port}; connect via SSH tunnel.', flush=True)
        server.serve_forever()
    except (OSError, ValueError, KeyError):
        print('Dashboard startup failed; check password setup, config, and port.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if 'server' in locals(): server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
