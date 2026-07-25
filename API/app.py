from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from db.firebase import bucket
import io
import os

# instantiate the app
app = Flask(__name__)
app.config.from_object(__name__)

# enable CORS
CORS(app, resources={r'/*': {'origins': '*'}})


# sanity check route
@app.route('/', methods=['GET'])
def home():
    return jsonify('Hello World')

@app.route('/ping', methods=['GET'])
def ping_pong():
    return jsonify('pong!')


@app.route('/upload', methods=['POST'])
def upload_file():
    if bucket is None:
        return jsonify('Firebase storage is not configured'), 503

    if 'file' not in request.files:
        return 'No file part'

    file = request.files['file']
    if file.filename == '':
        return 'No selected file'

    # Upload file to Firebase Storage
    blob = bucket.blob(file.filename)
    blob.upload_from_file(file)

    return 'File uploaded successfully'


# Endpoint to serve JPEG image files
@app.route('/images')
def serve_jpeg_image():
    if bucket is None:
        return jsonify('Firebase storage is not configured'), 503

    dest = os.path.join(os.path.dirname(__file__), 'db', 'image1.jpg')
    blob = bucket.blob('image1.jpg')
    blob.download_to_filename(dest)

    return send_file(dest, mimetype='image/jpeg')

if __name__ == '__main__':
    app.run(debug=True, port=5000)