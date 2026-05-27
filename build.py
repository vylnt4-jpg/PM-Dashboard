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

# ── NOTION API ───────────────────────────────────────────────────────────────

_title_cache = {}

def get_page_title(page_id):
    """Fetch Sprint page title, with cache."""
    if page_id in _title_cache:
        return _title_cache[page_id]
    try:
        r = requests.get(
            f'https://api.notion.com/v1/pages/{page_id}',
            headers=HEADERS, timeout=10
        )
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
    """Extract plain value from any Notion property type."""
    if not prop:
        return ''
    t = prop.get('type', '')
    if t == 'title':
        return ''.join(p.get('plain_text','') for p in prop.get('title', []))
    if t == 'rich_text':
        return ''.join(p.get('plain_text','') for p in prop.get('rich_text', []))
    if t == 'select':
        s = prop.get('select')
        return s['name'] if s else ''
    if t == 'multi_select':
        return ', '.join(s['name'] for s in prop.get('multi_select', []))
    if t == 'date':
        d = prop.get('date')
        return d['start'] if d else ''
    if t == 'number':
        n = prop.get('number')
        return str(n) if n is not None else ''
    if t == 'people':
        return ', '.join(p.get('name','') for p in prop.get('people', []))
    if t == 'formula':
        f = prop.get('formula', {})
        ft = f.get('type', '')
        if ft == 'number':
            n = f.get('number')
            return str(n) if n is not None else ''
        if ft == 'string':
            return f.get('string') or ''
    if t == 'relation':
        rels = prop.get('relation', [])
        return get_page_title(rels[0]['id']) if rels else ''
    if t == 'rollup':
        rollup = prop.get('rollup', {})
        if rollup.get('type') == 'number':
            n = rollup.get('number')
            return str(n) if n is not None else ''
    return ''

def query_all():
    """Fetch all pages from database (handles pagination)."""
    results, payload = [], {'page_size': 100}
    while True:
        r = requests.post(
            f'https://api.notion.com/v1/databases/{DB_ID}/query',
            headers=HEADERS, json=payload, timeout=30
        )
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

def page_to_row(page):
    props = page.get('properties', {})
    def p(name): return extract(props.get(name))

    # Sprint: relation → fetch title → clean URL artifacts
    sprint = re.sub(r'\s*\(https?://[^)]+\)', '', p('Sprint')).strip()

    # Status: normalize Testing → In Review
    status = p('Overall status')
    if status.lower() == 'testing':
        status = 'In Review'

    # Task name: try 'Task name' first, fallback to title-type property
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

# ── BUILD ────────────────────────────────────────────────────────────────────

def build():
    if not TOKEN:
        print('ERROR: NOTION_TOKEN not set'); sys.exit(1)

    print('📡 Fetching Notion database...')
    pages = query_all()
    print(f'✅ {len(pages)} pages fetched')

    rows = [page_to_row(p) for p in pages]
    rows = [r for r in rows if r['Task name']]   # drop empty rows
    print(f'✅ {len(rows)} valid rows')

    json_str = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    print(f'✅ JSON: {len(json_str):,} chars')

    # Read template
    with open('template.html', 'r', encoding='utf-8') as f:
        template = f.read()

    # Inject data into placeholder
    new_tag = f'<script id="d">const __D__={json_str};</script>'
    output, n = re.subn(
        r'<script id="d">const __D__=.*?;</script>',
        new_tag, template, count=1, flags=re.DOTALL
    )
    if n == 0:
        print('ERROR: injection point not found in template.html'); sys.exit(1)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(output)

    print(f'✅ Built index.html ({len(output):,} chars)')
    print('🎉 Done!')

if __name__ == '__main__':
    build()
