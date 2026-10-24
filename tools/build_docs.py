"""Build the dependency-free documentation site from local source only.

Run: python tools/build_docs.py
Check generated output: python tools/build_docs.py --check
"""
import argparse
import ast
import json
from pathlib import Path
import runpy
import textwrap

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
CLASSES = {
    'account_client.py': ['AccountClient'],
    'account_media.py': ['AccountMedia'],
    'account_profiles.py': ['AccountProfiles'],
    'account_updates.py': ['AccountUpdates'],
    'account_queue.py': ['AccountQueue'],
    'queue_store.py': ['SQLiteQueueStore'],
    'router.py': ['Router'],
    'client.py': ['Client'],
}
MODEL_FILES = ['account_client.py', 'account_peers.py', 'account_messages.py',
               'account_operations.py', 'account_files.py', 'account_profiles.py',
               'account_updates.py', 'queue_store.py']


def catalog():
    methods, models = [], []
    for filename, names in CLASSES.items():
        tree = ast.parse((ROOT / 'fnceitaa' / filename).read_text(encoding='utf-8'))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef) or cls.name not in names:
                continue
            for item in cls.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name.startswith('_') and item.name != '__init__':
                    continue
                args = ast.arguments(posonlyargs=item.args.posonlyargs,
                    args=item.args.args[1:], vararg=item.args.vararg,
                    kwonlyargs=item.args.kwonlyargs, kw_defaults=item.args.kw_defaults,
                    kwarg=item.args.kwarg, defaults=item.args.defaults)
                is_property = any(isinstance(d, ast.Name) and d.id == 'property' for d in item.decorator_list)
                signature = f'{cls.name}.{item.name}'
                if not is_property:
                    signature += f'({ast.unparse(args)})'
                if item.returns is not None:
                    signature += ' -> ' + ast.unparse(item.returns)
                methods.append(dict(owner=cls.name, name=item.name,
                    signature=signature,
                    asynchronous=isinstance(item, ast.AsyncFunctionDef),
                    property=is_property,
                    doc=ast.get_docstring(item) or '', source=f'fnceitaa/{filename}', line=item.lineno))
    for filename in MODEL_FILES:
        tree = ast.parse((ROOT / 'fnceitaa' / filename).read_text(encoding='utf-8'))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef) or cls.name.startswith('_'):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                       and d.func.id == 'dataclass' for d in cls.decorator_list):
                continue
            fields = [dict(name=f.target.id, type=ast.unparse(f.annotation))
                      for f in cls.body if isinstance(f, ast.AnnAssign)
                      and isinstance(f.target, ast.Name) and not f.target.id.startswith('_')]
            models.append(dict(name=cls.name, fields=fields, source=f'fnceitaa/{filename}'))
    return methods, models


def validate_content(pages, methods, summaries):
    """Catch stale names, missing translations and invalid examples, offline."""
    identifiers = [page['id'] for page in pages]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate page identifier')
    example_count = 0
    for page in pages:
        for value in (page['title'], page['description']):
            if not all(value.get(language) for language in ('fa', 'en')):
                raise ValueError(f'Missing page translation: {page["id"]}')
        sections = set()
        for block in page['blocks']:
            if 'text' in block and not all(block['text'].get(lang) for lang in ('fa', 'en')):
                raise ValueError(f'Missing block translation: {page["id"]}')
            if block['type'] == 'heading':
                if block['id'] in sections:
                    raise ValueError(f'Duplicate section: {page["id"]}/{block["id"]}')
                sections.add(block['id'])
            elif block['type'] == 'code' and block['language'] == 'python':
                wrapped = 'async def documentation_example():\n' + textwrap.indent(block['value'], '    ')
                compile(wrapped, f'{page["id"]}/{block["label"]}', 'exec')
                example_count += 1
            elif block['type'] == 'resources':
                for link in block['links']:
                    if not (DOCS / link['path']).is_file():
                        raise ValueError(f'Missing documentation resource: {link["path"]}')
    for method in methods:
        if not all(summaries.get(method['name'], {}).get(lang) for lang in ('fa', 'en')):
            raise ValueError(f'Missing method summary: {method["owner"]}.{method["name"]}')
    return example_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = runpy.run_path(str(DOCS / 'site_content.py'))
    methods, models = catalog()
    example_count = validate_content(content['PAGES'], methods, content['SUMMARIES'])
    project = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    # pyproject metadata is a simple literal; do not require tomllib on Python 3.10.
    version = next(line.split('=', 1)[1].strip().strip('"')
                   for line in project.splitlines() if line.startswith('version ='))
    data = dict(version=version, updated='2026-10-24', pages=content['PAGES'],
                methods=methods, models=models, summaries=content['SUMMARIES'])
    result = '// Generated by tools/build_docs.py; edit docs/site_content.py instead.\n'
    result += 'window.FNC_DOCS = ' + json.dumps(data, ensure_ascii=False, indent=2) + ';\n'
    target = DOCS / 'assets' / 'content.js'
    if args.check:
        if not target.exists() or target.read_text(encoding='utf-8') != result:
            raise SystemExit('Documentation catalog is stale. Run python tools/build_docs.py')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result, encoding='utf-8', newline='\n')
    print(f'Documentation: {len(data["pages"])} bilingual pages, {len(methods)} API entries, '
          f'{len(models)} models, {example_count} Python examples syntax-checked. OK')


if __name__ == '__main__':
    main()
