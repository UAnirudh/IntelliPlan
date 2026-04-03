from flask import Blueprint, render_template, request, session, redirect, url_for
from studentvue_helper import test_login, get_assignments

studentvue = Blueprint('studentvue', __name__, url_prefix='/studentvue')

@studentvue.route('/')
def sv_home():
    if session.get('login_type') != 'studentvue':
        return redirect(url_for('studentvue.sv_login'))
    return render_template('sv_home.html')

@studentvue.route('/login', methods=['GET', 'POST'])
def sv_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        district_url = request.form.get('district_url')

        if test_login(district_url, username, password):
            session['login_type'] = 'studentvue'
            session['sv_username'] = username
            session['sv_password'] = password
            session['sv_district_url'] = district_url
            return redirect(url_for('studentvue.sv_home'))
        else:
            error = "Invalid StudentVUE credentials."

    return render_template('login_studentvue.html', error=error)

@studentvue.route('/schedule')
def sv_schedule():
    if session.get('login_type') != 'studentvue':
        return redirect(url_for('studentvue.sv_login'))

    username = session['sv_username']
    password = session['sv_password']
    district_url = session['sv_district_url']

    data = get_assignments(district_url, username, password)
    return data