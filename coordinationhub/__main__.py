"""``python -m coordinationhub`` entry point — delegates to :mod:`cli`.

Lets users invoke the CLI without depending on the installed
``coordinationhub`` console script (e.g. inside a tox env or a CI
runner where the script may not be on ``PATH``).
"""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    sys.exit(main() or 0)
