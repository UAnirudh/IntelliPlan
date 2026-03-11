import flask
from flask import render_template
import requests
import os
from dotenv import load_dotenv
import TestData
load_dotenv()  # Load environment variables from .env file

CANVAS_TOKEN = os.getenv("CANVAS_TOKEN")
CANVAS_BASE = "https://canvas.instructure.com/api/v1"
app = flask.Flask(__name__)

# @app.route('/')
# def hello():
#     return 'Hello, World!'

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/schedule')
def data():
    return flask.jsonify(TestData.generate_schedule(TestData.assignments))

@app.route('/courses')
def get_courses():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    response = requests.get(f"{CANVAS_BASE}/courses", headers=headers)
    return flask.jsonify(response.json())

@app.route('/assignments')
def get_assignments():
    headers = {"Authorization": f"Bearer {CANVAS_TOKEN}"}
    response = requests.get(f"{CANVAS_BASE}/courses/14365743/assignments", headers=headers)
    return flask.jsonify(response.json())
if __name__ == '__main__':
    app.run(debug=True)