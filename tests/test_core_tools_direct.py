import os
import sys
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import llms.extensions.core_tools as core_tools

# Mock g_ctx
core_tools.g_ctx = MagicMock()


class TestCoreToolsDirect(unittest.TestCase):
    def test_calc(self):
        print("Running tests...")

        # Simple list comprehension
        res = core_tools.calc("sum([x * 2 for x in [1, 2, 3]])")
        print(f"sum([x * 2 for x in [1, 2, 3]]) = {res}")
        assert res == 12

        # List comprehension with condition
        res = core_tools.calc("sum([x for x in [1, 2, 3, 4] if x > 2])")
        print(f"sum([x for x in [1, 2, 3, 4] if x > 2]) = {res}")
        assert res == 7

        # Range support (I added range support too)
        res = core_tools.calc("sum([x for x in range(5)])")
        print(f"sum([x for x in range(5)]) = {res}")
        assert res == 10

        print("All tests passed!")


if __name__ == "__main__":
    unittest.main()
