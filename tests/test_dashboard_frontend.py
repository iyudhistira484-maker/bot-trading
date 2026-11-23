"""Static contracts between dashboard templates and their scripts."""
from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path('src/dashboard/static')


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.views = []
        self.labels = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if 'data-view' in attrs:
            self.views.append(attrs['data-view'])
        if tag == 'label' and 'for' in attrs:
            self.labels.append(attrs['for'])


def test_dashboard_template_matches_script_ids_and_navigation():
    page = Page()
    page.feed((STATIC / 'app.html').read_text())
    js = (STATIC / 'app.js').read_text()
    assert len(page.ids) == len(set(page.ids))
    for name in re.findall(r"\$\('([a-z0-9-]+)'\)", js):
        assert name in page.ids, f'missing template id {name}'
    assert set(page.views) == {'home', 'positions', 'history', 'settings', 'control'}
    assert all('view-' + name in page.ids for name in page.views)
    assert set(page.labels).issubset(page.ids)


def test_login_template_matches_script_ids():
    page = Page()
    page.feed((STATIC / 'login.html').read_text())
    js = (STATIC / 'login.js').read_text()
    for name in re.findall(r"getElementById\('([a-z0-9-]+)'\)", js):
        assert name in page.ids
    assert len(page.ids) == len(set(page.ids))
    assert set(page.labels).issubset(page.ids)
