import os

from firebase_admin import credentials, initialize_app, storage

# Path to the Firebase Admin service-account JSON key.
# Set GOOGLE_APPLICATION_CREDENTIALS (or FIREBASE_CREDENTIALS) to point at your
# local copy; the key is intentionally not committed to this repo.
CRED_PATH = os.environ.get('FIREBASE_CREDENTIALS') or os.environ.get(
    'GOOGLE_APPLICATION_CREDENTIALS'
)

BUCKET_NAME = os.environ.get('FIREBASE_STORAGE_BUCKET', 'openpose-db.appspot.com')

# When the credential is missing we leave the bucket as None instead of raising,
# so the sanity routes still come up and only the storage routes are disabled.
bucket = None

if CRED_PATH and os.path.exists(CRED_PATH):
    cred = credentials.Certificate(CRED_PATH)
    initialize_app(cred)
    bucket = storage.bucket(BUCKET_NAME)
else:
    print(
        'WARNING: Firebase credentials not found. '
        'Set FIREBASE_CREDENTIALS to your service-account JSON path. '
        'Upload and image routes are disabled.'
    )
