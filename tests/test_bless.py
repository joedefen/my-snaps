"""Identity tests for this project's vendored release gate, and its inspector.

`bless` is emitted from `~/Projects/blessd/bless` and is never edited here. The
gate's own policy -- the branch plan, the prompt, the index check, the build,
the emitter -- is tested in `blessd`, once, where it is written. What is left
for this project to answer is the identity the tree derives from its own
`pyproject.toml` and the distribution inspector the release uploads behind.
"""

import importlib.machinery
import importlib.util
import io
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


bless = load_module('bless_gate', ROOT / 'bless')
inspect_dist = load_module('inspect_dist', ROOT / 'tests' / 'inspect_dist.py')

PROJECT = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
VERSION = (ROOT / 'my_snaps' / '__init__.py').read_text(encoding='utf-8')
VERSION = next(line.split('"')[1] for line in VERSION.splitlines()
               if line.startswith('__version__'))


# --- identity: read from this tree, never written into the tool ------------

def make_project(root, name, *, module=None):
    """A throwaway tree shaped the way `identity()` expects to find one."""
    root.mkdir(parents=True, exist_ok=True)
    package = module or name.replace('-', '_').replace('.', '_')
    (root / package).mkdir()
    (root / package / '__init__.py').write_text('__version__ = "1.2.3"\n')
    table = ['[project]', f'name = "{name}"', 'dynamic = ["version"]']
    if module:
        table += ['', '[tool.bless]', f'module = "{module}"']
    (root / 'pyproject.toml').write_text('\n'.join(table) + '\n')
    return root


def test_the_identity_is_read_from_the_project_table(tmp_path):
    told = bless.identity(make_project(tmp_path / 'plain', 'some-proj'))

    assert told['name'] == 'some-proj'
    # `-` becomes `_`: the name is written once, the package derived from it.
    assert told['module'] == 'some_proj'
    assert told['console'] is None
    assert told['inspector'] == 'tests/inspect_dist.py'
    # No `[tool.bless] branches` means the default branch model.
    assert told['branches'] == ('main', 'dev')


def test_the_tool_table_overrides_the_derivation(tmp_path):
    root = make_project(tmp_path / 'odd', 'some-proj', module='odd_name')
    assert bless.identity(root)['module'] == 'odd_name'


def test_a_missing_package_is_refused_with_both_remedies(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "nope"\n')

    with pytest.raises(bless.BlessError) as caught:
        bless.identity(tmp_path)
    message = str(caught.value)
    assert 'nope/__init__.py' in message
    assert '[tool.bless]' in message and 'module = "the_package"' in message


def test_a_package_without_a_version_line_is_refused(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "quiet"\n')
    (tmp_path / 'quiet').mkdir()
    (tmp_path / 'quiet' / '__init__.py').write_text('"""no version here"""\n')

    with pytest.raises(bless.BlessError, match='__version__'):
        bless.identity(tmp_path)


def test_the_version_is_read_without_importing_the_package(tmp_path):
    root = make_project(tmp_path / 'read', 'readable')
    assert bless.read_version(root, 'readable') == '1.2.3'


def test_this_tree_derives_its_own_identity():
    name = PROJECT['name']

    assert bless.PROJECT['name'] == name
    assert bless.PROJECT['module'] == name.replace('-', '_')
    assert bless.PROJECT['console'] in list(PROJECT['scripts'])
    assert bless.PROJECT['console'] == 'my-snaps'
    assert bless.PROJECT['inspector'] == 'tests/inspect_dist.py'
    # This project has a development branch, so it keeps the default model.
    assert bless.PROJECT['branches'] == ('main', 'dev')
    assert bless.PYPI_URL == f'https://pypi.org/pypi/{name}/json'
    assert bless.TESTPYPI_URL == f'https://test.pypi.org/pypi/{name}/json'
    assert bless.read_version() == VERSION


# --- the shared inspector ------------------------------------------------

def make_wheel(path, *, summary=None, dependencies=None, include_tests=False,
               modules=True):
    metadata = [
        'Metadata-Version: 2.4',
        'Name: my-snaps',
        f'Version: {VERSION}',
        f'Summary: {summary or PROJECT["description"]}',
        f'Requires-Python: {PROJECT["requires-python"]}',
        *[f'Requires-Dist: {dependency}' for dependency in
          (dependencies if dependencies is not None
           else PROJECT['dependencies'])],
        f'Keywords: {", ".join(PROJECT.get("keywords", []))}',
        'License-Expression: MIT',
        'License-File: LICENSE',
        '',
        'A description body.',
    ]
    names = ([f'my_snaps/{module.name}'
              for module in (ROOT / 'my_snaps').glob('*.py')] if modules else [])
    names.append(f'my_snaps-{VERSION}.dist-info/licenses/LICENSE')
    if include_tests:
        names.append('tests/test_smoke.py')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(f'my_snaps-{VERSION}.dist-info/METADATA',
                         '\n'.join(metadata))
        for name in names:
            archive.writestr(name, '')
    return path


def make_sdist(path, *, version=VERSION, include_tests=False):
    base = f'my_snaps-{version}'
    members = {
        f'{base}/pyproject.toml': '',
        f'{base}/README.md': '',
        f'{base}/LICENSE': '',
        f'{base}/my_snaps/__init__.py': f'__version__ = "{version}"\n',
        f'{base}/my_snaps/main.py': '',
    }
    if include_tests:
        members[f'{base}/tests/test_smoke.py'] = ''
    with tarfile.open(path, 'w:gz') as archive:
        for name, text in members.items():
            payload = text.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_a_correct_wheel_and_sdist_pass(tmp_path):
    inspect_dist.check_all(make_wheel(tmp_path / 'ok.whl'),
                           make_sdist(tmp_path / 'ok.tar.gz'))


def test_the_inspector_rejects_a_summary_that_is_not_this_project(tmp_path):
    wheel = make_wheel(tmp_path / 'summary.whl',
                       summary='A GTK tray applet for power management')
    with pytest.raises(inspect_dist.DistributionError) as caught:
        inspect_dist.check_wheel(wheel)
    assert 'Summary' in str(caught.value)


def test_the_inspector_rejects_an_undeclared_runtime_dependency(tmp_path):
    wheel = make_wheel(tmp_path / 'extra.whl',
                       dependencies=[*PROJECT['dependencies'], 'pydbus>=0.6.0'])
    with pytest.raises(inspect_dist.DistributionError) as caught:
        inspect_dist.check_wheel(wheel)
    assert 'Requires-Dist' in str(caught.value)


def test_the_inspector_keeps_wheel_contents_honest(tmp_path):
    with_tests = make_wheel(tmp_path / 'tests.whl', include_tests=True)
    with pytest.raises(inspect_dist.DistributionError, match='tests/'):
        inspect_dist.check_wheel(with_tests)

    bare = make_wheel(tmp_path / 'bare.whl', modules=False)
    with pytest.raises(inspect_dist.DistributionError, match='missing'):
        inspect_dist.check_wheel(bare)


def test_the_sdist_must_carry_the_package_and_its_version(tmp_path):
    mismatch = make_sdist(tmp_path / 'old.tar.gz', version='1.0.0')
    with pytest.raises(inspect_dist.DistributionError, match='1.0.0'):
        inspect_dist.check_sdist(mismatch)

    # The rehearsal overrides the version, and only that.
    inspect_dist.check_sdist(make_sdist(tmp_path / 'old.tar.gz',
                                        version='2.0.2.99.0'),
                             version='2.0.2.99.0')
