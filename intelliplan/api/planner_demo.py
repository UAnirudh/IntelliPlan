"""Public fabricated planner demonstration with no server-side learner state."""
from flask import Blueprint, make_response, render_template

bp = Blueprint('planner_demo', __name__)


@bp.get('/demo/planner')
def demo():
    response = make_response(render_template('planner_demo.html'))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'none'; img-src 'none'; font-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
    return response
