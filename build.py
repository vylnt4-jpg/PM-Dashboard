#!/usr/bin/env python3
"""
build.py — Fetch Notion database → inject JSON → build index.html
Runs in GitHub Actions with NOTION_TOKEN secret.
"""
import json, os, re, sys
import requests

TOKEN    = os.environ.get('NOTION_TOKEN', '')
DB_ID    = '3570e4e2394280ff87c5c23d138eb3c3'
HEADERS  = {
    'Authorization': f'Bearer {TOKEN}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json'
}
COLS = ['Sprint','Overall status','Due date','Velocity','Priority','Task name','Assignee','Tags']

# ── NOTION API ────────────────────────────────────────────

_title_cache = {}

def get_page_title(page_id):
    """Fetch Sprint page title, with cache."""
    if page_id in _title_cache:
        return _title_cache[page_id]
    try:
        r = requests.get(
            f'https://api.notion.com/v1/pages/{page_id}',
            headers=HEADERS, timeout=10)
        for prop in r.json().get('properties', {}).values():
            if prop.get('type') == 'title':
                texts = prop.get('title', [])
                if texts:
                    title = texts[0].get('plain_text', '')
                    _title_cache[page_id] = title
                    return title
    except Exception as e:
        print(f'  warn: get_page_title({page_id}): {e}')
    _title_cache[page_id] = ''
    return ''

def extract(prop):
    """Extract a plain-text value from a Notion property object."""
    if not prop:
        return ''
    t = prop.get('type', '')
    if t == 'title':
        items = prop.get('title', [])
        return items[0].get('plain_text', '') if items else ''
    if t == 'rich_text':
        items = prop.get('rich_text', [])
        return items[0].get('plain_text', '') if items else ''
    if t == 'select':
        s = prop.get('select')
        return s.get('name', '') if s else ''
    if t == 'multi_select':
        return ', '.join(o.get('name', '') for o in prop.get('multi_select', []))
    if t == 'date':
        d = prop.get('date')
        return d.get('start', '') if d else ''
    if t == 'number':
        v = prop.get('number')
        return str(v) if v is not None else ''
    if t == 'people':
        people = prop.get('people', [])
        names = [p.get('name', '') for p in people if p.get('name')]
        return ', '.join(names)
    if t == 'formula':
        f = prop.get('formula', {})
        ft = f.get('type', '')
        if ft == 'string': return f.get('string', '') or ''
        if ft == 'number': v = f.get('number'); return str(v) if v is not None else ''
        if ft == 'boolean': return str(f.get('boolean', ''))
        return ''
    if t == 'relation':
        rels = prop.get('relation', [])
        return get_page_title(rels[0]['id']) if rels else ''
    if t == 'rollup':
        ro = prop.get('rollup', {})
        rt = ro.get('type', '')
        if rt == 'number':
            v = ro.get('number')
            return str(v) if v is not None else ''
        if rt == 'array':
            arr = ro.get('array', [])
            parts = [extract(item) for item in arr if extract(item)]
            return ', '.join(parts)
        return ''
    if t == 'checkbox':
        return 'Yes' if prop.get('checkbox') else 'No'
    if t == 'url':
        return prop.get('url', '') or ''
    if t == 'email':
        return prop.get('email', '') or ''
    if t == 'phone_number':
        return prop.get('phone_number', '') or ''
    if t == 'status':
        s = prop.get('status')
        return s.get('name', '') if s else ''
    return ''

# ── PAGINATION ────────────────────────────────────────────

def query_all():
    results, payload = [], {'page_size': 100}
    while True:
        r = requests.post(
            f'https://api.notion.com/v1/databases/{DB_ID}/query',
            headers=HEADERS, json=payload, timeout=30)
        if r.status_code != 200:
            print(f'ERROR {r.status_code}: {r.text[:300]}')
            sys.exit(1)
        data = r.json()
        results.extend(data.get('results', []))
        print(f'  fetched {len(results)} pages...')
        if not data.get('has_more'):
            break
        payload['start_cursor'] = data['next_cursor']
    return results

# ── ROW MAPPING ───────────────────────────────────────────

def page_to_row(page):
    props = page.get('properties', {})
    def p(name): return extract(props.get(name))

    sprint = re.sub(r'\s*\(https?://[^)]+\)', '', p('Sprint')).strip()

    status = p('Overall status')
    if status.lower() == 'testing':
        status = 'In Review'

    task_name = p('Task name')
    if not task_name:
        for prop in props.values():
            if prop.get('type') == 'title':
                task_name = extract(prop)
                break

    return {
        'Sprint':          sprint,
        'Overall status':  status,
        'Due date':        p('Due date'),
        'Velocity':        p('Velocity'),
        'Priority':        p('Priority'),
        'Task name':       task_name,
        'Assignee':        p('Assignee'),
        'Tags':            p('Tags'),
    }

# ── BUILD ─────────────────────────────────────────────────

def build():
    if not TOKEN:
        print('ERROR: NOTION_TOKEN not set'); sys.exit(1)

    print('📡 Fetching Notion database...')
    pages = query_all()
    print(f'✅ {len(pages)} pages fetched')

    rows = [page_to_row(p) for p in pages]
    rows = [r for r in rows if r['Task name']]
    print(f'✅ {len(rows)} valid rows')

    # ensure_ascii=True: escape all non-ASCII as \uXXXX so JSON is safe inside <script> tags
    json_str = json.dumps(rows, ensure_ascii=True, separators=(',', ':'))
    print(f'✅ JSON: {len(json_str):,} chars')

    with open('template.html', 'r', encoding='utf-8') as f:
        template = f.read()

    new_tag = f'<script id="d">const __D__={json_str};</script>'

    # Use lambda so regex does NOT process backslashes in replacement string
    output, n = re.subn(
        r'<script id="d">const __D__=.*?;</script>',
        lambda m: new_tag,
        template, count=1, flags=re.DOTALL
    )
    if n == 0:
        print('ERROR: injection point not found in template.html'); sys.exit(1)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(output)
    print(f'✅ Built index.html ({len(output):,} chars)')
    print('🎉 Done!')

if __name__ == '__main__':
    build()
