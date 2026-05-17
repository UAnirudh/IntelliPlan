from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
import requests

canvas = Blueprint('canvas', __name__, url_prefix='/canvas')

@canvas.route('/')
def canvas_home():
    if session.get('login_type') != 'canvas':
        return redirect(url_for('canvas.canvas_login'))
    return render_template('canvas_home.html')

@canvas.route('/login', methods=['GET', 'POST'])
def canvas_login():
    error = None
    if request.method == 'POST':
        token = request.form.get('canvas_token')
        url = request.form.get('canvas_url')

        try:
            test = requests.get(
                f"{url}/api/v1/courses",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
        except requests.RequestException as e:
            error = f"Could not reach Canvas: {e}"
            return render_template('login.html', error=error)

        if test.status_code == 200:
            session['login_type'] = 'canvas'
            session['canvas_token'] = token
            session['canvas_url'] = url
            return redirect(url_for('canvas.canvas_home'))
        else:
            error = "Invalid Canvas token or URL."

    return render_template('login.html', error=error)

@canvas.route('/schedule')
def canvas_schedule():
    if session.get('login_type') != 'canvas':
        return redirect(url_for('canvas.canvas_login'))

    token = session.get('canvas_token')
    url = session.get('canvas_url')
    if not token or not url:
        return redirect(url_for('canvas.canvas_login'))
    base = f"{url}/api/v1"

    try:
        resp = requests.get(
            f"{base}/courses",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except requests.RequestException as e:
        return jsonify({"error": f"Canvas unreachable: {e}"}), 502
    if resp.status_code >= 400:
        return jsonify({"error": "Canvas API error", "status": resp.status_code}), resp.status_code
    try:
        courses = resp.json()
    except ValueError:
        return jsonify({"error": "Canvas returned non-JSON response"}), 502

    return jsonify({"courses": courses})