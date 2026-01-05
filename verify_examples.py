import sys
import os
from unittest.mock import MagicMock

# Add project root to path
# We are likely running from /home/mythz/src/ServiceStack/llms
# The tests folder is /home/mythz/src/ServiceStack/llms/tests
# so we need to add the current dir to path or parent.
# reproduce_list_comp.py was in tests/ and did: sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# If I run this from root, I can just ensure '.' is in path.

sys.path.append(os.getcwd())

try:
    import llms.extensions.core_tools as core_tools
except ImportError:
    # Fallback if running from a subdir
    sys.path.append(os.path.abspath(os.path.join(os.getcwd(), "..")))
    import llms.extensions.core_tools as core_tools

# Mock g_ctx
core_tools.g_ctx = MagicMock()

examples = [
    # Leibniz formula for Pi approximation
    "4 * sum([((-1)**k) / (2*k + 1) for k in range(100)])",
    # Filtering: Multiples of 3 or 5 below 20
    "sum([x for x in range(20) if x % 3 == 0 or x % 5 == 0])",
    # Square roots formatted (rounding)
    "[round(sqrt(x), 2) for x in range(1, 6)]",
    # Standard Deviation of squares
    "stdev([x**2 for x in range(10)])",
    # Generating powers of 2
    "[2**i for i in range(10)]",
]

for ex in examples:
    try:
        print(f"Expr: {ex}")
        res = core_tools.calc(ex)
        print(f"Result: {res}")
    except Exception as e:
        print(f"Failed: {ex} -> {e}")
