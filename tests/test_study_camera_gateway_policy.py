"""Network gateway CSP is explicit, bounded and scoped to the study page."""
import App


def test_gateway_policy_only_expands_active_page(monkeypatch):
    monkeypatch.setenv('CSP_ENFORCE', '1')
    monkeypatch.setenv('STUDY_CAMERA_GATEWAY_ORIGINS', 'https://camera.example:8889')
    with App.app.test_client() as client:
        active = client.get('/active')
        login = client.get('/login')
    assert 'https://camera.example:8889' in active.headers['Content-Security-Policy']
    assert 'https://camera.example:8889' not in login.headers['Content-Security-Policy']


def test_gateway_origins_cannot_inject_directives_or_wildcards(monkeypatch):
    monkeypatch.setenv('STUDY_CAMERA_GATEWAY_ORIGINS', ','.join([
        'https://camera.example/', 'https://[::1]:8889', 'https://bad; script-src *',
        'https://*.example', 'https://u:p@camera.example', 'http://camera.example',
        'https://camera.example/path', 'https://camera.example:99999', 'https://camera.example:0',
        'https://camera.example?x', 'https://camera.example\nscript-src *']))
    assert App._study_camera_gateway_origins() == ['https://camera.example', 'https://[::1]:8889']
