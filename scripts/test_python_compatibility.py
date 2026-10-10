import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PythonCompatibilityTests(unittest.TestCase):
    def test_all_project_sources_parse_on_python_3_10(self):
        for path in ROOT.rglob("*.py"):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))


if __name__ == "__main__":
    unittest.main()
