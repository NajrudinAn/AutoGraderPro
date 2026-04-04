import cv2
import numpy as np
import json
import os
import csv
from src import image_core
from src import region_scanner

VIRTUAL_W = 1000
VIRTUAL_H = 1400

def load_configs():
    if os.path.exists("configs.json"):
        try:
            with open("configs.json", "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"blueprints": []}

def load_matrices():
    if os.path.exists("master_answers.json"):
        try:
            with open("master_answers.json", "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"keys": []}

def prompt_error_visual(title, text):
    diag = np.zeros((300, 800, 3), dtype=np.uint8)
    diag[:] = (30, 30, 30) # Dark gray
    
    cv2.putText(diag, title, (20, 50), cv2.FONT_HERSHEY_DUPLEX, 1.2, (0, 100, 255), 2)
    
    words = text.split(' ')
    lines = []
    cl = ""
    for w in words:
        if len(cl) + len(w) < 55:
            cl += w + " "
        else:
            lines.append(cl)
            cl = w + " "
    lines.append(cl)
    
    y = 120
    for l in lines:
        cv2.putText(diag, l.strip(), (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
        y += 35
        
    cv2.putText(diag, "Press ANY KEY to terminate.", (20, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)
    
    cv2.imshow(title, diag)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

def execute_pipeline(filepath, active_bp, active_truth):
    fname = os.path.basename(filepath)
    print(f"Loading vector stream: {fname}...")
    
    frame = cv2.imread(filepath)
    if frame is None: return

    anchors = image_core.locate_anchor_points(frame)
    if len(anchors) != 4:
        print(f">> ERROR: Structural geometry failed in {fname}")
        return

    # Draw neon blue diagnostic lines connecting corners
    ordered = image_core.sort_coordinates(anchors)
    for i in range(4):
        p1 = tuple(np.int32(ordered[i]))
        p2 = tuple(np.int32(ordered[(i+1)%4]))
        cv2.line(frame, p1, p2, (255, 200, 0), 2)
        cv2.circle(frame, p1, 10, (0, 0, 255), -1)

    mat = image_core.calculate_warp_matrix(anchors, VIRTUAL_W, VIRTUAL_H)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    score, data_dump, graphics = region_scanner.evaluate_template_zones(
        gray, mat, active_bp.get("zones", []), active_truth
    )

    print(f">> Result Computed: {score} out of {len(active_truth)}")

    gt, rc, inf, track, blue_exp = graphics
    image_core.render_evaluation_graphics(frame, gt, rc, inf, track, blue_exp)
    
    # 5. draw overlay info
    text_buffer = []
    for k, v in data_dump.items():
        if v["type"] == "Info":
            val = "".join(v["data"])
            text_buffer.append(f"{v['label']}: {val}")
    
    sy = 50
    for line in text_buffer:
        cv2.rectangle(frame, (20, sy - 30), (450, sy + 10), (0, 0, 0), -1)
        cv2.putText(frame, line, (30, sy), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        sy += 50

    # 6. High-tech Score Box on top-right
    hx = frame.shape[1] - 350
    hy = 30
    cv2.rectangle(frame, (hx, hy), (hx + 300, hy + 130), (20, 20, 20), -1)
    cv2.putText(frame, "EVALUATION SCORE", (hx + 20, hy + 30), cv2.FONT_HERSHEY_DUPLEX, 0.6, (150, 150, 150), 1)
    
    t_score = f"{score}"
    cv2.putText(frame, t_score, (hx + 20, hy + 100), cv2.FONT_HERSHEY_DUPLEX, 2.0, (0, 255, 0), 3)
    cv2.putText(frame, f"/ {len(active_truth)}", (hx + 110, hy + 100), cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2)

    disp = cv2.resize(frame, (0,0), fx=0.5, fy=0.5)
    cv2.imshow(f"Output: {fname}", disp)
    cv2.waitKey(1) 

    if not os.path.exists("processed_exports"): os.makedirs("processed_exports")
    out_img = f"processed_exports/verified_{fname}"
    cv2.imwrite(out_img, frame)
    
    out_csv = f"processed_exports/report_{fname}.csv"
    with open(out_csv, "w", newline="") as f:
        writer = csv.writer(f)
        
        for line in text_buffer:
            if ":" in line:
                lbl, val = line.split(":", 1)
                writer.writerow([lbl.strip(), val.strip()])
        writer.writerow([])
        
        writer.writerow(["Question", "Expected", "Scanned", "Validity"])
        
        aq = []
        for k, v in data_dump.items():
            if v["type"] == "Questions":
                aq.extend(v["data"])
                
        if aq:
            aq.sort(key=lambda x: x["Question"])
            for r in aq:
                writer.writerow([r["Question"], r["Correct Answer"], r["Given Answer"], r["Mark"]])
            
        writer.writerow([])
        writer.writerow(["FINAL_SCORE", score])

if __name__ == "__main__":
    conf = load_configs()
    bps = conf.get("blueprints", [])
    
    if not bps:
        msg = "No blueprints found in configs.json. Run app.py to initiate the Configuration Utility."
        print(msg)
        prompt_error_visual("INITIALIZATION HALTED", msg)
        exit(1)
        
    active_bp = next((b for b in bps if b.get("is_default")), bps[0])
    
    mat_db = load_matrices()
    keys = mat_db.get("keys", [])
    
    if not keys:
        msg = "No truth matrices found in master_answers.json. Sync a master sheet in app.py first."
        print(msg)
        prompt_error_visual("SYNC REQUIRED", msg)
        exit(1)
        
    active_key = keys[0]
    truth = active_key["answers"]
    
    print(f"System Ready. Using blueprint '{active_bp['name']}' with {len(truth)} vectors.")

    dir_in = "input_docs"
    if not os.path.exists(dir_in):
        os.makedirs(dir_in)
        
    found = False
    for f in os.listdir(dir_in):
        if f.lower().endswith((".jpg", ".png", ".jpeg")):
            found = True
            execute_pipeline(os.path.join(dir_in, f), active_bp, truth)
            
    if not found:
        print(f"Directory {dir_in}/ is empty. Insert target documents.")
        
    if found:
        print("Pipeline execution completed. Press ANY KEY on rendering window to terminate.")
        cv2.waitKey(0)
        cv2.destroyAllWindows()
