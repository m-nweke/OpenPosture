# OpenPosture - Sitting Posture Feedback System

### [Run Guide](RUNNING.md)
### [Model Download Link](ModelReadME.md)
### [Project Proposal Slide deck](Presentations/PostureCapstone.pptx)
### [Statement of Work](Misc/SoW_Posture.docx)

## Overview
This MIT-licensed software, hosted on GitHub, is a posture assessment tool that determines the sitting position of a person when given a lateral view as input. The output includes details such as the position of the back (straight, hunchback, reclined), position of the hands (folded vs not folded), and kneeling (i.e., feet curled behind the knees). The project employs a trained Keras model based on OpenPose to detect keypoints on the human body, providing valuable insights into sitting posture.

## Importance
Poor sitting posture can lead to lower back and neck pain, as well as various adverse health effects, including musculoskeletal imbalances, balance issues, impaired digestion, and reduced flexibility. Good posture, such as keeping feet flat on the floor, avoiding crossing knees or ankles, and sitting up straight, is crucial for overall well-being.

## Project Goals
### New Features:
Determine the position of the neck.
Identify if feet are on the ground or dangling.
Detect if legs are crossed.
Provide recommendations for posture improvement to reduce the risk of back and neck pain.
### User Interface:
Develop an easy-to-use UI for users to view their posture status and receive corresponding recommendations.

## Expected Outcomes
Design a seated posture assessment interface that evaluates the alignment of the back, feet, knees, and neck. Provide personalized recommendations to minimize the risk of neck and back discomfort.

## Model Architecture
The project uses a VGG-like architecture with a multi-stage approach (stages 1 to 6) to progressively refine predictions. The model focuses on detecting keypoints on the human body, including joints like the head, shoulders, elbows, wrists, hips, knees, and ankles. It incorporates branches for both Part Affinity Fields (PAF) and confidence maps. Predictions from prior stages are concatenated with the input for iterative refinement. The model is designed for training with additional inputs such as vector weights and heat weights, utilizing ReLU activation, concatenation, and multiplication operations.

## Technologies Used
Frontend: Vue.js

Database: Firebase

Backend: Python API via OpenPose

Model: Keras/TensorFlow

Computer Vision: OpenCV

Development Environment: Jupyter Notebook

## Contributors
1. [Michael Nweke](https://github.com/m-nweke)
2. [Ally Ryan](https://github.com/aerc4d)
3. [Parisha Rathod](https://github.com/parisha8994)
