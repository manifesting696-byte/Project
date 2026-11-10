"""One concise acceptance pass; --live adds one actual model query."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from tests.acceptance import main
if __name__ == "__main__":
    main()
