"""Export existing private runtime settings to an ignored local env file."""
import argparse
from pathlib import Path
from app.env_settings import LocalEnv, exported
from app.security import Platform


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--live', action='store_true', help='Explicitly enable actual bot delivery in the exported file.')
    parser.add_argument('--replace', action='store_true', help='Replace an existing private file after saving your backup.')
    args = parser.parse_args()
    root = Path(args.state).resolve()
    output = Path(args.output).resolve()
    if not (root/'registry.sqlite3').is_file():
        raise SystemExit('Existing state directory is required; no new state was initialized.')
    if not output.name.startswith('.env') or output.name in ('.env.example', '.env.sample'):
        raise SystemExit('Choose a private .env.local file, never a public template.')
    if output.exists() and not args.replace:
        raise SystemExit('Output already exists; back it up before using --replace.')
    store = LocalEnv(output, process={})
    values = exported(Platform(root), live=args.live)
    with store.change(values):
        pass
    print('Private environment file saved. No credential values were printed.')


if __name__ == '__main__':
    main()
