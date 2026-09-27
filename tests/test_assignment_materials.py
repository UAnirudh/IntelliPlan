"""Canvas schoolwork enters the tutor only by student choice and within bounds."""

import pytest

import assignment_materials as materials


ORIGIN = 'https://school.instructure.com'


class Response:
    def __init__(self, payload=None, *, status=200, headers=None, body=b'', links=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.body = body
        self.links = links or {}

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_assignment_extracts_only_canvas_file_links_and_does_not_leak_token(monkeypatch):
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host', lambda url: True)
    seen = []

    def get(url, **kwargs):
        seen.append((url, kwargs))
        if url.endswith('/courses/4/assignments/8'):
            return Response({'id': 8, 'course_id': 4, 'name': 'Fractions',
                             'description': '<p>Compare fractions.</p><a href="/courses/4/files/17/download">Worksheet</a>'
                                            '<a href="https://evil.example/files/99">Ignore</a>'})
        if url.endswith('/files/17'):
            return Response({'id': 17, 'display_name': 'fractions.txt', 'size': 16,
                             'content-type': 'text/plain', 'url': 'https://cdn.example/worksheet'})
        if url == 'https://cdn.example/worksheet':
            assert 'headers' not in kwargs
            return Response(body=b'1/2 is greater than 1/3')
        raise AssertionError(url)

    monkeypatch.setattr(materials.requests, 'get', get)
    result = materials.load_assignment(ORIGIN, 'secret-token', '4', '8')
    assert result['title'] == 'Fractions'
    assert result['description'] == 'Compare fractions. Worksheet Ignore'
    assert result['materials'] == [{'name': 'fractions.txt', 'text': '1/2 is greater than 1/3'}]
    assert result['total_files'] == 1
    assert len(seen) == 3
    assert all(call[1].get('headers', {}).get('Authorization') == 'Bearer secret-token' for call in seen[:2])
    assert 'secret-token' not in materials.assignment_prompt(result)


@pytest.mark.parametrize('course_id,assignment_id', [('4/other', '8'), ('4', '8?x'), ('-1', '8')])
def test_rejects_non_numeric_assignment_ids_before_network(course_id, assignment_id, monkeypatch):
    monkeypatch.setattr(materials.requests, 'get', lambda *a, **k: pytest.fail('unexpected network'))
    with pytest.raises(materials.MaterialError):
        materials.load_assignment(ORIGIN, 'token', course_id, assignment_id)


def test_wrong_assignment_response_is_rejected(monkeypatch):
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host', lambda url: True)
    monkeypatch.setattr(materials.requests, 'get', lambda *a, **k: Response({'id': 9, 'course_id': 4}))
    with pytest.raises(materials.MaterialError, match='different assignment'):
        materials.load_assignment(ORIGIN, 'token', '4', '8')


def test_expired_file_scope_gives_reconnect_guidance(monkeypatch):
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host', lambda url: True)
    monkeypatch.setattr(materials.requests, 'get', lambda *a, **k: Response(status=403))
    with pytest.raises(materials.MaterialError, match='Reconnect Canvas'):
        materials.load_assignment(ORIGIN, 'token', '4', '8')


def test_private_redirect_is_blocked_before_second_request(monkeypatch):
    urls = []
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host',
                        lambda url: '127.0.0.1' not in url)

    def get(url, **kwargs):
        urls.append(url)
        return Response(status=302, headers={'Location': 'https://127.0.0.1/private'})

    monkeypatch.setattr(materials.requests, 'get', get)
    with pytest.raises(materials.MaterialError, match='unsafe'):
        materials._download_public_file('https://cdn.example/file')
    assert urls == ['https://cdn.example/file']


def test_streaming_limit_stops_unadvertised_large_file(monkeypatch):
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host', lambda url: True)
    monkeypatch.setattr(materials.requests, 'get',
                        lambda *a, **k: Response(body=b'x' * (materials.MAX_FILE_BYTES + 1)))
    with pytest.raises(materials.MaterialError, match='too large'):
        materials._download_public_file('https://cdn.example/file')


def test_unreadable_attachment_is_reported_without_failing_directions(monkeypatch):
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host', lambda url: True)

    def get(url, **kwargs):
        if url.endswith('/assignments/8'):
            return Response({'id': 8, 'course_id': 4, 'name': 'Lab',
                             'description': '<p>Explain your observation.</p><a href="/files/17">Image</a>'})
        return Response({'id': 17, 'display_name': 'diagram.png', 'size': 20,
                         'content-type': 'image/png', 'url': 'https://cdn.example/image'})

    monkeypatch.setattr(materials.requests, 'get', get)
    result = materials.load_assignment(ORIGIN, 'token', '4', '8')
    assert result['description'].startswith('Explain your observation.')
    assert result['materials'] == []
    assert result['skipped_count'] == 1
    assert 'could not be read' in materials.assignment_prompt(result)


def test_docx_expansion_limit_rejects_archive_bomb():
    from io import BytesIO
    import zipfile

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', b'x' * (10 * 1024 * 1024 + 1))
    with pytest.raises(materials.MaterialError, match='expands'):
        materials._extract_file(buf.getvalue(), 'docx')


def test_assignment_picker_includes_no_due_date_and_refuses_cross_origin_page(monkeypatch):
    monkeypatch.setattr(materials.net_guard, 'resolves_to_public_host', lambda url: True)
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        if '/courses?' in url:
            return Response([{'id': 4, 'name': 'History'}])
        return Response([{'id': 8, 'name': 'Ongoing project', 'due_at': None}],
                        links={'next': {'url': 'https://evil.example/api/v1/other'}})

    monkeypatch.setattr(materials.requests, 'get', get)
    with pytest.raises(materials.MaterialError, match='outside'):
        materials.list_assignments(ORIGIN, 'token')
    assert len(seen) == 2

    monkeypatch.setattr(materials.requests, 'get',
                        lambda url, **kwargs: Response([{'id': 4, 'name': 'History'}])
                        if '/courses?' in url else
                        Response([{'id': 8, 'name': 'Ongoing project', 'due_at': None}]))
    assert materials.list_assignments(ORIGIN, 'token')[0]['due_date'] == ''
