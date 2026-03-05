import flask
from flask import render_template
import TestData
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

if __name__ == '__main__':
    app.run(debug=True)