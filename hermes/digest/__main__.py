"""Entry point: python -m hermes.digest [--post]

Default is a console dry run (prints the digest, posts nothing).
--post sends it to #the-boss. The 7 AM LaunchAgent runs with --post.
"""
import argparse
import logging
import sys

from dotenv import load_dotenv

from . import deliver, sweep

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hermes.digest")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true",
                    help="post to Slack #the-boss (default: print only)")
    args = ap.parse_args()

    data = sweep.collect()
    text = deliver.build_text(data)
    print(text)

    if args.post:
        deliver.post(data)
        log.info("Digest posted to #the-boss")
    return 0


if __name__ == "__main__":
    sys.exit(main())
