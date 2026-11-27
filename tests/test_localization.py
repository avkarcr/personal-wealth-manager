"""Static RU/EN contracts. User-entered data is checked through the HTTP tests."""
import ast
from pathlib import Path
import re
import unittest

from jinja2 import Environment, nodes
from wealth_manager.adapters.web.i18n import EN


class LocalizationTests(unittest.TestCase):
    def check_labels(self, path, strings):
        missing = {s for s in strings if isinstance(s, str) and re.search('[А-Яа-яЁё]', s) and s not in EN}
        self.assertEqual(sorted(missing), [], str(path))

    def test_template_labels(self):
        env = Environment()
        for path in Path('templates').rglob('*.html'):
            with self.subTest(path=path):
                tree = env.parse(path.read_text(encoding='utf-8'))
                # Includes built-in labels passed through lists and field macros.
                self.check_labels(path, (n.value for n in tree.find_all(nodes.Const)))

    def test_builtin_instrument_catalog_and_research_messages(self):
        modules = Path('wealth_manager/modules')
        persistence = Path('wealth_manager/adapters/persistence')
        paths = [modules / name / 'domain.py' for name in ('companies', 'taxes', 'macroeconomics')]
        for name in ('instruments', 'market_catalog'):
            paths.extend((modules / name).glob('*.py'))
        paths.extend(persistence / name for name in ('research.py', 'instruments.py', 'market_catalog.py'))
        paths.append(Path('wealth_manager/shared/records.py'))
        for path in paths:
            with self.subTest(path=path):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                self.check_labels(path, (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)))
