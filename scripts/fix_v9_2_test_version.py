from pathlib import Path

path = Path('tests/test_v9_2_native_upload.py')
text = path.read_text(encoding='utf-8')
old = '        self.assertEqual(s.build_status()["version"], "9.2.0")\n'
new = (
    '        expected_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()\n'
    '        self.assertEqual(s.build_status()["version"], expected_version)\n'
)
if text.count(old) != 1:
    raise RuntimeError(f'expected one hard-coded version assertion, found {text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('made v9.2 native-upload test version-agnostic')
