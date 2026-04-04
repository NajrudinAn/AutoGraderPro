import cv2
import numpy as np

# core math stuff that makes this work

def locate_anchor_points(image, min_size=300, tile_size=31, weight=5):
    # block size needs to be odd for some reason
    if tile_size % 2 == 0:
        tile_size += 1
    
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, tile_size, weight)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape
    dots = []
    
    for c in contours:
        if cv2.contourArea(c) > min_size:
            (x,y), _ = cv2.minEnclosingCircle(c)
            dots.append((int(x), int(y)))
            
    if len(dots) < 4: 
        return np.array([])
        
    dots = np.array(dots)
    corners = np.array([[0,0], [w,0], [0,h], [w,h]])
    valid_anchors = []
    
    # grab the closest dot to each corner so we don't reuse them
    used = set()
    for corner in corners:
        distances = np.linalg.norm(dots - corner, axis=1)
        closest_indices = np.argsort(distances)
        for idx in closest_indices:
            if idx not in used:
                valid_anchors.append(dots[idx])
                used.add(idx)
                break
                
    if len(valid_anchors) != 4:
        return np.array([])
        
    return np.array(valid_anchors)

def sort_coordinates(points):
    box = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    box[0] = points[np.argmin(sums)] 
    box[2] = points[np.argmax(sums)] 
    diffs = np.diff(points, axis=1)
    box[1] = points[np.argmin(diffs)] 
    box[3] = points[np.argmax(diffs)] 
    return box

def calculate_warp_matrix(corners, target_w, target_h):
    ordered = sort_coordinates(corners)
    target_pts = np.array([
        [0, 0], [target_w - 1, 0], 
        [target_w - 1, target_h - 1], [0, target_h - 1]], dtype="float32")
    return cv2.getPerspectiveTransform(target_pts, ordered)

def project_pixel(matrix, x, y):
    pt = np.array([[[x, y]]], dtype="float32")
    real = cv2.perspectiveTransform(pt, matrix)
    return (int(real[0][0][0]), int(real[0][0][1]))

def auto_center_marker(gray_img, x, y, size):
    buffer = int(size * 1.2)
    left = max(0, x - size - buffer)
    top = max(0, y - size - buffer)
    right = min(gray_img.shape[1], x + size + buffer)
    bottom = min(gray_img.shape[0], y + size + buffer)
    
    crop = gray_img[top:bottom, left:right]
    if crop.size == 0: return x, y, size

    blurred = cv2.GaussianBlur(crop, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    best_loc = None
    max_mass = 0
    center_x = (right - left) // 2
    center_y = (bottom - top) // 2
    target_area = np.pi * (size ** 2)
    
    for c in cnts:
        mass = cv2.contourArea(c)
        if mass > target_area * 0.15 and mass < target_area * 2.5:
            (cx, cy), c_r = cv2.minEnclosingCircle(c)
            dist = np.sqrt((cx - center_x)**2 + (cy - center_y)**2)
            if dist < size * 1.5: 
                if mass > max_mass:
                    max_mass = mass
                    best_loc = (int(cx), int(cy))

    if best_loc:
        return left + best_loc[0], top + best_loc[1], size
    return x, y, size

def render_evaluation_graphics(canvas, green_ticks, red_crosses, info_fills, tracking_boxes, blue_expected=None):
    overlay = canvas.copy()
    
    # 1. Info fields just get standard colored backgrounds
    for (x, y, r, color) in info_fills:
        cv2.circle(overlay, (int(x), int(y)), int(r), color, -1)
        
    alpha = 0.5
    cv2.addWeighted(overlay, alpha, canvas, 1 - alpha, 0, canvas)
    
    # 2. Tracking boxes (faint dashed rectangles instead of rings)
    for (x, y, r, color) in tracking_boxes:
        size = int(r * 1.2)
        cv2.rectangle(canvas, (x - size, y - size), (x + size, y + size), color, 1)

    # 3. Green checkmarks for correct hits
    for (x, y, r) in green_ticks:
        # Green outline square
        size = int(r * 1.5)
        cv2.rectangle(canvas, (x - size, y - size), (x + size, y + size), (0, 200, 0), 3)
        # Checkmark
        pts = np.array([[x - 8, y], [x - 2, y + 6], [x + 10, y - 8]], np.int32)
        pts = pts.reshape((-1, 1, 2))
        cv2.polylines(canvas, [pts], False, (0, 200, 0), 4)

    # 4. Red crosses for wrong hits
    for (x, y, r) in red_crosses:
        size = int(r * 1.3)
        # Bounding box
        cv2.rectangle(canvas, (x - size, y - size), (x + size, y + size), (0, 0, 200), 2)
        # X mark
        cv2.line(canvas, (x - 6, y - 6), (x + 6, y + 6), (0, 0, 255), 3)
        cv2.line(canvas, (x + 6, y - 6), (x - 6, y + 6), (0, 0, 255), 3)

    # 5. Blue circles showing the expected correct answer when student got it wrong
    if blue_expected:
        for (x, y, r) in blue_expected:
            size = int(r * 1.5)
            cv2.circle(canvas, (x, y), size, (255, 150, 0), 3)
            # small arrow pointing down into the bubble
            cv2.line(canvas, (x, y - size - 6), (x, y - size + 4), (255, 150, 0), 3)
            cv2.line(canvas, (x - 5, y - size - 1), (x, y - size + 4), (255, 150, 0), 2)
            cv2.line(canvas, (x + 5, y - size - 1), (x, y - size + 4), (255, 150, 0), 2)
