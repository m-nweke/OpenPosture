## Project Setup
Create your virtual environment
```sh
python3.11 -m venv env
```

### Activate Virtual Environment

```sh
source env/bin/activate
```

### Install Dependencies

```sh
pip install -r requirements.txt
```
### Run model examples
#### You can update sample image for testing in posture_image.py on line 246
``canvas, position = process('./sample_images/OP73.jpeg', params, model_params)``
```
python posture_image.py OR python posture_realtime.py
```
### Start API  for Development (for backend to frontend communication)

```sh
python -m flask run
```
