from flask import Blueprint, render_template, request, session, redirect, url_for
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

        test = requests.get(f"{url}/api/v1/courses",
                            headers={"Authorization": f"Bearer {token}"})

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

    token = session['canvas_token']
    url = session['canvas_url']
    base = f"{url}/api/v1"

    courses = requests.get(f"{base}/courses",
                           headers={"Authorization": f"Bearer {token}"}).json()

    return courses