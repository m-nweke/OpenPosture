import cv2
import math
import time
import numpy as np
import util
from config_reader import config_reader
from scipy.ndimage import gaussian_filter
from model import get_testing_model

tic = 0
# visualize
colors = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0],
          [0, 255, 0], \
          [0, 255, 85], [0, 255, 170], [0, 255, 255], [0, 170, 255], [0, 85, 255], [0, 0, 255],
          [85, 0, 255], \
          [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85]]


def process(input_image, params, model_params):
    ''' Start of finding the Key points of full body using Open Pose.'''
    oriImg = cv2.imread(input_image)  # B,G,R order
    multiplier = [x * model_params['boxsize'] / oriImg.shape[0] for x in params['scale_search']]
    heatmap_avg = np.zeros((oriImg.shape[0], oriImg.shape[1], 19))
    paf_avg = np.zeros((oriImg.shape[0], oriImg.shape[1], 38))
    for m in range(1):
        scale = multiplier[m]
        imageToTest = cv2.resize(oriImg, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        imageToTest_padded, pad = util.padRightDownCorner(imageToTest, model_params['stride'],
                                                          model_params['padValue'])
        input_img = np.transpose(np.float32(imageToTest_padded[:, :, :, np.newaxis]),
                                 (3, 0, 1, 2))  # required shape (1, width, height, channels)
        output_blobs = model.predict(input_img)
        heatmap = np.squeeze(output_blobs[1])  # output 1 is heatmaps
        heatmap = cv2.resize(heatmap, (0, 0), fx=model_params['stride'], fy=model_params['stride'],
                             interpolation=cv2.INTER_CUBIC)
        heatmap = heatmap[:imageToTest_padded.shape[0] - pad[2], :imageToTest_padded.shape[1] - pad[3],
                  :]
        heatmap = cv2.resize(heatmap, (oriImg.shape[1], oriImg.shape[0]), interpolation=cv2.INTER_CUBIC)
        paf = np.squeeze(output_blobs[0])  # output 0 is PAFs
        paf = cv2.resize(paf, (0, 0), fx=model_params['stride'], fy=model_params['stride'],
                         interpolation=cv2.INTER_CUBIC)
        paf = paf[:imageToTest_padded.shape[0] - pad[2], :imageToTest_padded.shape[1] - pad[3], :]
        paf = cv2.resize(paf, (oriImg.shape[1], oriImg.shape[0]), interpolation=cv2.INTER_CUBIC)
        heatmap_avg = heatmap_avg + heatmap / len(multiplier)
        paf_avg = paf_avg + paf / len(multiplier)

    all_peaks = []  # To store all the key points which a re detected.
    peak_counter = 0

    prinfTick(1)  # prints time required till now.

    for part in range(18):
        map_ori = heatmap_avg[:, :, part]
        map = gaussian_filter(map_ori, sigma=3)

        map_left = np.zeros(map.shape)
        map_left[1:, :] = map[:-1, :]
        map_right = np.zeros(map.shape)
        map_right[:-1, :] = map[1:, :]
        map_up = np.zeros(map.shape)
        map_up[:, 1:] = map[:, :-1]
        map_down = np.zeros(map.shape)
        map_down[:, :-1] = map[:, 1:]

        peaks_binary = np.logical_and.reduce(
            (map >= map_left, map >= map_right, map >= map_up, map >= map_down, map > params['thre1']))
        peaks = list(zip(np.nonzero(peaks_binary)[1], np.nonzero(peaks_binary)[0]))  # note reverse
        peaks_with_score = [x + (map_ori[x[1], x[0]],) for x in peaks]
        id = range(peak_counter, peak_counter + len(peaks))
        peaks_with_score_and_id = [peaks_with_score[i] + (id[i],) for i in range(len(id))]

        all_peaks.append(peaks_with_score_and_id)
        peak_counter += len(peaks)

    connection_all = []
    special_k = []
    mid_num = 10

    prinfTick(2)  # prints time required till now.
    print()
    position = checkPosition(all_peaks)  # check position of spine.
    checkKneeling(all_peaks)  # check whether kneeling oernot
    checkHandFold(all_peaks)  # check whether hands are folding or not.
    neck_posture = evaluate_neck_posture(all_peaks)  # check neck position
    print(neck_posture)
    feet_position = evaluate_feet_position(all_peaks)
    print(feet_position)
    canvas1 = draw(input_image, all_peaks)  # show the image.
    return canvas1, position


def draw(input_image, all_peaks):
    canvas = cv2.imread(input_image)  # B,G,R order
    for i in range(18):
        for j in range(len(all_peaks[i])):
            cv2.circle(canvas, all_peaks[i][j][0:2], 4, colors[i], thickness=-1)
    return canvas


def checkPosition(all_peaks):
    try:
        f = 0
        if (all_peaks[16]):
            a = all_peaks[16][0][0:2]  # Right Ear
            f = 1
        else:
            a = all_peaks[17][0][0:2]  # Left Ear
        b = all_peaks[11][0][0:2]  # Hip
        angle = calcAngle(a, b)
        degrees = round(math.degrees(angle))
        if (f):
            degrees = 180 - degrees
        if (degrees < 70):
            return 1
        elif (degrees > 110):
            return -1
        else:
            return 0
    except Exception as e:
        print("person not in lateral view and unable to detect ears or hip")


# calculate angle between two points with respect to x-axis (horizontal axis)
def calcAngle(a, b):
    try:
        ax, ay = a
        bx, by = b
        if (ax == bx):
            return 1.570796
        return math.atan2(by - ay, bx - ax)
    except Exception as e:
        print("unable to calculate angle")


def checkHandFold(all_peaks):
    try:
        if (all_peaks[3][0][0:2]):
            try:
                if (all_peaks[4][0][0:2]):
                    distance = calcDistance(all_peaks[3][0][0:2],
                                            all_peaks[4][0][0:2])  # distance between right arm-joint and right palm.
                    armdist = calcDistance(all_peaks[2][0][0:2],
                                           all_peaks[3][0][0:2])  # distance between left arm-joint and left palm.
                    if (distance < (armdist + 100) and distance > (
                            armdist - 100)):  # this value 100 is arbitary. this shall be replaced with a calculation which can adjust to different sizes of people.
                        print("Not Folding Hands")
                    else:
                        print("Folding Hands")
            except Exception as e:
                print("Folding Hands")
    except Exception as e:
        try:
            if (all_peaks[7][0][0:2]):
                distance = calcDistance(all_peaks[6][0][0:2], all_peaks[7][0][0:2])
                armdist = calcDistance(all_peaks[6][0][0:2], all_peaks[5][0][0:2])
                # print(distance)
                if (distance < (armdist + 100) and distance > (armdist - 100)):
                    print("Not Folding Hands")
                else:
                    print("Folding Hands")
        except Exception as e:
            print("Unable to detect arm joints")


def calcDistance(a, b):  # calculate distance between two points.
    try:
        x1, y1 = a
        x2, y2 = b
        return math.hypot(x2 - x1, y2 - y1)
    except Exception as e:
        print("unable to calculate distance")


def checkKneeling(all_peaks):
    f = 0
    if (all_peaks[16]):
        f = 1
    try:
        if (all_peaks[10][0][0:2] and all_peaks[13][0][0:2]):  # if both legs are detected
            rightankle = all_peaks[10][0][0:2]
            leftankle = all_peaks[13][0][0:2]
            hip = all_peaks[11][0][0:2]
            leftangle = calcAngle(hip, leftankle)
            leftdegrees = round(math.degrees(leftangle))
            rightangle = calcAngle(hip, rightankle)
            rightdegrees = round(math.degrees(rightangle))
        if (f == 0):
            leftdegrees = 180 - leftdegrees
            rightdegrees = 180 - rightdegrees
        if (
                leftdegrees > 60 and rightdegrees > 60):  # 60 degrees is trail and error value here. We can tweak this accordingly and results will vary.
            print("Both Legs are in Kneeling")
        elif (rightdegrees > 60):
            print("Right leg is kneeling")
        elif (leftdegrees > 60):
            print("Left leg is kneeling")
        else:
            print("Not kneeling")

    except IndexError as e:
        try:
            if (f):
                a = all_peaks[10][0][0:2]  # if only one leg (right leg) is detected
            else:
                a = all_peaks[13][0][0:2]  # if only one leg (left leg) is detected
            b = all_peaks[11][0][0:2]  # location of hip
            angle = calcAngle(b, a)
            degrees = round(math.degrees(angle))
            if (f == 0):
                degrees = 180 - degrees
            if (degrees > 60):
                print("Both Legs Kneeling")
            else:
                print("Not Kneeling")
        except Exception as e:
            print("legs not detected")

def evaluate_neck_posture(all_peaks):
    # Indices for the neck and shoulders based on model's keypoint layout
    neck_index = 1
    r_shoulder_index = 2
    l_shoulder_index = 5

    try:
        # Get key points for neck and shoulders
        neck = all_peaks[neck_index][0][:2] if len(all_peaks[neck_index]) > 0 else None
        r_shoulder = all_peaks[r_shoulder_index][0][:2] if len(all_peaks[r_shoulder_index]) > 0 else None
        l_shoulder = all_peaks[l_shoulder_index][0][:2] if len(all_peaks[l_shoulder_index]) > 0 else None

        # Calculate the midpoint of the shoulders to estimate the shoulder center
        if r_shoulder and l_shoulder:
            shoulder_center = [(r_shoulder[0] + l_shoulder[0]) / 2, (r_shoulder[1] + l_shoulder[1]) / 2]
        else:
            return "Shoulder keypoints not detected."

        if neck and shoulder_center:
            # Evaluate the posture of the neck
            neck_posture = "Undefined"
            # The threshold values and evaluation logic can be fine-tuned as needed
            if abs(neck[1] - shoulder_center[1]) < 10:  # If neck y is close to shoulder center y
               neck_posture = "Neck is Straight"
            elif neck[1] < shoulder_center[1]:
               neck_posture = "Neck is Forward"
            elif neck[1] > shoulder_center[1]:
                neck_posture = "Neck is Backward"
            return neck_posture
        else:
            return "Neck keypoints not detected."
    except Exception as e:
        return str(e)

def evaluate_feet_position(all_peaks):
    """
    Evaluate if feet are on the ground based on relative positions to other body parts.
    :param all_peaks: Detected keypoints.
    :return: Evaluation of feet position as string.
    """
    # Indices for keypoints
    r_ankle_index = 10
    l_ankle_index = 13
    r_knee_index = 9
    l_knee_index = 12

    # Extract keypoints for ankles and knees
    r_ankle = all_peaks[r_ankle_index][0][:2] if len(all_peaks[r_ankle_index]) > 0 else None
    l_ankle = all_peaks[l_ankle_index][0][:2] if len(all_peaks[l_ankle_index]) > 0 else None
    r_knee = all_peaks[r_knee_index][0][:2] if len(all_peaks[r_knee_index]) > 0 else None
    l_knee = all_peaks[l_knee_index][0][:2] if len(all_peaks[l_knee_index]) > 0 else None

    # Determine the evaluation of feet position based on knee and ankle relative positions
    feet_on_floor = "Unknown"
    if r_ankle and l_ankle and r_knee and l_knee:
        # Assume feet are on the floor if the ankles are below the knees
        if r_ankle[1] > r_knee[1] and l_ankle[1] > l_knee[1]:
            feet_on_floor = "Both feet are on the floor."
        else:
            feet_on_floor = "One or both feet are not on the floor."
    elif r_ankle or l_ankle:
        # If only one ankle is detected, check if it is below its corresponding knee
        feet_partially_on_floor = []
        if r_ankle and r_knee and r_ankle[1] > r_knee[1]:
            feet_partially_on_floor.append("right foot")
        if l_ankle and l_knee and l_ankle[1] > l_knee[1]:
            feet_partially_on_floor.append("left foot")
        if feet_partially_on_floor:
            feet_on_floor = f"At least {' and '.join(feet_partially_on_floor)} is on the floor."
        else:
            feet_on_floor = "Feet are not on the floor."
    else:
        feet_on_floor = "Feet keypoints not detected."

    return feet_on_floor

def showimage(
        img):  # sometimes opencv will oversize the image when using using `cv2.imshow()`. This function solves that issue.
    screen_res = 1280, 720  # my screen resolution.
    scale_width = screen_res[0] / img.shape[1]
    scale_height = screen_res[1] / img.shape[0]
    scale = min(scale_width, scale_height)
    window_width = int(img.shape[1] * scale)
    window_height = int(img.shape[0] * scale)
    cv2.namedWindow('image', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('image', window_width, window_height)
    cv2.imshow('image', img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()




def prinfTick(i):  # Time calculation to keep a trackm of progress
    toc = time.time()
    print('Processed Position %d in %.5f' % (i, toc - tic))


if __name__ == '__main__':  # main function of the program
    tic = time.time()
    print('Starting posture assessment...')

    model = get_testing_model()
    model.load_weights('./model/keras/model.h5')

    vi = False
    if (vi == False):
        time.sleep(2)
        params, model_params = config_reader()
        canvas, position = process('./sample_images/OP55.jpeg', params, model_params)
        if position == 1:
            print("Hunchback position")
        elif position == -1:
            print("Reclined back position")
        else:
            print("Straight back position")

        showimage(canvas)

