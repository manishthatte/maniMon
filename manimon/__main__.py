"""`python3 -m manimon` — same entry point as the `manimon` command."""
import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())
