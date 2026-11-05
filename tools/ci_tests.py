"""Run the unit tests and surface each failure as a GitHub annotation."""
import os
import sys
import unittest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
suite = unittest.defaultTestLoader.discover(os.path.join(root, "tests"), top_level_dir=root)
result = unittest.TextTestRunner(verbosity=2).run(suite)
for test, tb in result.failures + result.errors:
    msg = tb.strip().replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-3000:]
    print(f"::error title={test.id()}::{msg}")
print(f"::notice title=tests::ran {result.testsRun}, failures {len(result.failures)}, "
      f"errors {len(result.errors)}, skipped {len(result.skipped)}")
sys.exit(0 if result.wasSuccessful() else 1)
