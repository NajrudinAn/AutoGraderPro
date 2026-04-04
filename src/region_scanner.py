import cv2
import numpy as np
from src import image_core

V_RADIUS = 10    
DENSITY_CUTOFF = 0.40   

def _calculate_ink_weight(img_gray, px, py, rad):
    """
    measures how filled the bubble is.
    uses clahe and thresholds to make faint marks visible.
    """
    cushion = 5
    px, py = int(px), int(py)
    x1 = max(0, px - rad - cushion)
    y1 = max(0, py - rad - cushion)
    x2 = min(img_gray.shape[1], px + rad + cushion)
    y2 = min(img_gray.shape[0], py + rad + cushion)
    
    patch = img_gray[y1:y2, x1:x2]
    if patch.size == 0:
        return 0.0
    
    # clahe: boosts contrast so faded bubbles show up better
    clahe_filter = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(4, 4))
    patch_boosted = clahe_filter.apply(patch)
    
    patch_smooth = cv2.GaussianBlur(patch_boosted, (5, 5), 0)
    
    # adaptive thresh
    binary_patch = cv2.adaptiveThreshold(
        patch_smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 13, 3
    )
    
    # make a circle mask
    cut_mask = np.zeros(binary_patch.shape, dtype="uint8")
    local_px = px - x1
    local_py = py - y1
    act_rad = max(1, rad - 1)
    cv2.circle(cut_mask, (local_px, local_py), act_rad, 255, -1)
    
    black_pixels = cv2.countNonZero(cv2.bitwise_and(binary_patch, binary_patch, mask=cut_mask))
    max_pixels = np.pi * (act_rad ** 2)
    return black_pixels / max_pixels if max_pixels > 0 else 0.0


def _search_for_nearby_blob(img_gray, x, y, known_rad, search_dist):
    """ tries to find the real center of a bubble nearby for the grid """
    cush = int(search_dist)
    x1, y1 = max(0, int(x - cush)), max(0, int(y - cush))
    x2, y2 = min(img_gray.shape[1], int(x + cush)), min(img_gray.shape[0], int(y + cush))
    sub_img = img_gray[y1:y2, x1:x2]
    if sub_img.size == 0: return None
    
    enhance = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    sub_clahe = enhance.apply(sub_img)
    sub_blur = cv2.GaussianBlur(sub_clahe, (5, 5), 0)
    sub_thresh = cv2.adaptiveThreshold(sub_blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4)
    blobs, _ = cv2.findContours(sub_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    ideal_area = np.pi * (known_rad ** 2)
    mid_x = x - x1
    mid_y = y - y1
    
    shortest_dist = float('inf')
    found_blob = None
    for b in blobs:
        size = cv2.contourArea(b)
        # let area vary cuz of glare and stuff
        if size > ideal_area * 0.10 and size < ideal_area * 2.5:
            (cx, cy), br = cv2.minEnclosingCircle(b)
            # see if it looks like a circle
            if br > known_rad * 0.3 and br < known_rad * 2.5:
                gap = np.sqrt((cx - mid_x)**2 + (cy - mid_y)**2)
                if gap <= search_dist:
                    if gap < shortest_dist:
                        shortest_dist = gap
                        found_blob = (int(x1 + cx), int(y1 + cy))
    return found_blob


def evaluate_template_zones(img_gray, M_matrix, zones, answer_dict):
    """
    pulls data from whatever zones we setup.
    returns total_points, data_dump, and graphics
    - graphics: green_ticks, red_crosses, info_fills, track_boxes
    """
    data_dump = {}
    
    green_ticks = []
    red_crosses = []
    info_fills = []
    track_boxes = []
    blue_expected = []
    
    total_points = 0
    all_zones_compiled = []

    # ---------------------------------------------------------
    # PART 1: make the grids and find anchors
    # ---------------------------------------------------------
    for z_idx, zone in enumerate(zones):
        z_id = zone.get("id", f"zone_{z_idx}")
        kind = zone.get("type", "questions")
        
        count = zone.get("num_items", 1)
        choices = zone.get("num_options", 4)
        labels = zone.get("options_list", ["A", "B", "C", "D"])
        
        orientation = zone.get("direction", "vertical")
        q_start = zone.get("start_q", 1)
        tag = zone.get("info_label", "InfoField")
        
        zx = zone.get("x", 0)
        zy = zone.get("y", 0)
        zw = zone.get("width", 100)
        zh = zone.get("height", 100)
        
        ox = zone.get("offset_x", 0.0)
        oy = zone.get("offset_y", 0.0)
        br = zone.get("bubble_radius", V_RADIUS)
        
        # STEP A: figure out where anchors should be
        mesh = []
        for i in range(count):
            row_points = []
            for j in range(choices):
                if orientation == "vertical":
                    u = (j + 0.5) / max(1, choices)
                    v = (i + 0.5) / max(1, count)
                else:
                    u = (i + 0.5) / max(1, count)
                    v = (j + 0.5) / max(1, choices)
                
                vcx = zx + (u * zw) + ox
                vcy = zy + (v * zh) + oy
                
                (ax, ay) = image_core.project_pixel(M_matrix, vcx, vcy)
                (ex, ey) = image_core.project_pixel(M_matrix, vcx + br, vcy)
                
                calc_rad = int(np.sqrt((ex - ax)**2 + (ey - ay)**2))
                calc_rad = max(5, min(calc_rad, 35))
                
                row_points.append((int(ax), int(ay), calc_rad))
            mesh.append(row_points)
            
        # STEP B: shift things around to fit
        zone_dx = []
        zone_dy = []
        row_shifts = {}
        
        for i in range(count):
            idx = []
            idy = []
            for j in range(choices):
                px, py, r = mesh[i][j]
                
                # look for a blob nearby
                scope = int(r * 1.5)
                hit = _search_for_nearby_blob(img_gray, px, py, r, scope)
                
                if hit:
                    dx, dy = hit[0] - px, hit[1] - py
                    idx.append(dx)
                    idy.append(dy)
                    zone_dx.append(dx)
                    zone_dy.append(dy)
            
            # if we found a blob, lock the y shift
            if idy:
                row_shifts[i] = np.median(idy)
                    
        # fallback if we cant see anything
        avg_dx = np.median(zone_dx) if zone_dx else 0.0
        avg_dy = np.median(zone_dy) if zone_dy else 0.0
        
        # update the grid with the shifts
        for i in range(count):
            idy = row_shifts.get(i, avg_dy)
            if avg_dx != 0 or idy != 0:
                for j in range(choices):
                    px, py, r = mesh[i][j]
                    mesh[i][j] = (int(px + avg_dx), int(py + idy), r)

        all_zones_compiled.append({
            "z_id": z_id,
            "kind": kind,
            "count": count,
            "choices": choices,
            "labels": labels,
            "q_start": q_start,
            "tag": tag,
            "mesh": mesh
        })

    # ---------------------------------------------------------
    # PART 2: read the bubbles
    # ---------------------------------------------------------
    for compiled in all_zones_compiled:
        mesh = compiled["mesh"]
        kind = compiled["kind"].lower()
        count = compiled["count"]
        choices = compiled["choices"]
        labels = compiled["labels"]
        q_start = compiled["q_start"]

        # STEP C: read density using the new grid
        z_results = []
        for i in range(count):
            val_id = q_start + i if kind == "questions" else i
            reads = []
            
            for j in range(choices):
                fx, fy, r = mesh[i][j]
                
                # draw tracking rectangles
                track_boxes.append((int(fx), int(fy), int(r), (150, 150, 150)))
                
                weight = _calculate_ink_weight(img_gray, fx, fy, r)
                reads.append((weight, j, fx, fy, r))

            reads.sort(key=lambda x: x[0], reverse=True)
            top = reads[0]
            runner_up = reads[1][0] if len(reads) > 1 else 0
            
            gate = DENSITY_CUTOFF
            if kind == "info" and top[0] >= 0.25 and (top[0] - runner_up) >= 0.15:
                gate = 0.25
                
            picked_idx = top[1] if top[0] > gate else -1
            picked_val = labels[picked_idx] if picked_idx != -1 and picked_idx < len(labels) else "-"

            if kind == "questions":
                truth = str(answer_dict.get(str(val_id), "")).strip()
                t_idx = -1
                
                # Check directly in labels
                if truth and truth in labels:
                    t_idx = labels.index(truth)
                elif truth:
                    # Attempt zero-indexed conversion
                    try:
                        n = int(truth)
                        if 0 <= n < len(labels):
                            t_idx = n
                    except ValueError:
                        pass
                    
                    # Attempt 1-indexed conversion if first failed
                    if t_idx == -1:
                        try:
                            n = int(truth)
                            if 1 <= n <= len(labels):
                                t_idx = n - 1
                        except ValueError:
                            pass
                
                correct_hit = (picked_idx == t_idx)
                if correct_hit and picked_idx != -1: 
                    total_points += 1
                
                if picked_idx != -1:
                    bx, by, br = int(top[2]), int(top[3]), int(top[4])
                    if correct_hit:
                        green_ticks.append((bx, by, br))
                    else:
                        red_crosses.append((bx, by, br))
                        # also mark the correct answer bubble in blue so teacher can see
                        if t_idx != -1 and t_idx < choices:
                            cx, cy, cr = mesh[i][t_idx]
                            blue_expected.append((int(cx), int(cy), int(cr)))
                    
                z_results.append({
                    "Question": val_id, 
                    "Given Answer": picked_val, 
                    "Correct Answer": labels[t_idx] if t_idx != -1 and t_idx < len(labels) else "-", 
                    "Mark": 1 if correct_hit else 0
                })

            elif kind == "info":
                if picked_idx != -1:
                    wx, wy, wr = int(top[2]), int(top[3]), int(top[4])
                    info_fills.append((wx, wy, wr, (200, 100, 50))) # Blueish background for info
                z_results.append(picked_val)

        data_dump[compiled["z_id"]] = {
            "type": kind.capitalize(),
            "label": compiled["tag"] if kind == "info" else f"Questions {q_start}-{q_start+count-1}",
            "data": z_results
        }
        
    return total_points, data_dump, (green_ticks, red_crosses, info_fills, track_boxes, blue_expected)
