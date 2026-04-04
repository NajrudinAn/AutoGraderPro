import os
import cv2
import json
import numpy as np
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from io import BytesIO

from src import image_core
from src import region_scanner

app = Flask(__name__)
CORS(app)  # so the frontend can talk to this server without browser blocking it

CANVAS_W = 1000
CANVAS_H = 1400

CONF_FILE = "configs.json"
KEYS_FILE = "master_answers.json"
RESULTS_FILE = "graded_results.json"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)  # make sure folder exists for saving images

# ---- helper functions to read/write json files ----
def get_configs():
    if os.path.exists(CONF_FILE):
        try:
            with open(CONF_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load {CONF_FILE}: {e}")
    return {"blueprints": []}

def save_configs(data):
    with open(CONF_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load {KEYS_FILE}: {e}")
    return {"keys": []}

def save_keys(data):
    with open(KEYS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_results():
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load {RESULTS_FILE}: {e}")
    return {"results": []}

def save_results(data):
    with open(RESULTS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def img_to_bytes(img_array):
    _, encoded = cv2.imencode('.jpg', img_array)
    return BytesIO(encoded.tobytes())

# ---- all the api routes start here ----

@app.route('/api/templates', methods=['GET'])
def list_templates():
    return jsonify(get_configs())

@app.route('/api/templates', methods=['POST'])
def save_template():
    data = request.json
    conf_data = get_configs()
    bp_name = data.get("name")
    make_default = data.get("is_default", False)
    zones = data.get("zones", [])

    idx = next((i for i, b in enumerate(conf_data["blueprints"]) if b["name"] == bp_name), -1)
    new_bp = {
        "name": bp_name,
        "is_default": make_default,
        "zones": zones
    }
    
    if idx >= 0:
        conf_data["blueprints"][idx] = new_bp
    else:
        conf_data["blueprints"].append(new_bp)
        
    if make_default:
        for b in conf_data["blueprints"]:
            if b["name"] != bp_name:
                b["is_default"] = False
                
    save_configs(conf_data)
    return jsonify({"success": True, "message": f"Template '{bp_name}' saved."})

@app.route('/api/templates/<name>/image', methods=['POST'])
def upload_template_image(name):
    """save the sheet image that goes with a template, using a unique id so files dont get overwritten"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400
    file = request.files['file']
    import uuid
    ext = os.path.splitext(file.filename)[1] if file.filename else '.jpg'
    unique_name = f"template_{uuid.uuid4().hex[:12]}{ext}"
    filepath = os.path.join(DATA_DIR, unique_name)
    file.save(filepath)
    
    # store the image filename in the blueprint config so we can find it later
    conf = get_configs()
    for bp in conf.get("blueprints", []):
        if bp["name"] == name:
            # delete the old image file if there was one
            old_img = bp.get("image_file")
            if old_img:
                old_path = os.path.join(DATA_DIR, old_img)
                if os.path.exists(old_path):
                    os.remove(old_path)
            bp["image_file"] = unique_name
            break
    save_configs(conf)
    
    return jsonify({"success": True, "path": filepath})

@app.route('/api/templates/<name>/image', methods=['GET'])
def get_template_image(name):
    """send back the saved sheet image for a template"""
    # first check if the blueprint has a stored image filename
    conf = get_configs()
    bp = next((b for b in conf.get("blueprints", []) if b["name"] == name), None)
    if bp and bp.get("image_file"):
        filepath = os.path.join(DATA_DIR, bp["image_file"])
        if os.path.exists(filepath):
            return send_file(filepath, mimetype='image/jpeg')
    
    # fallback to old naming convention for backwards compatibility
    filepath = os.path.join(DATA_DIR, f"template_{name}.jpg")
    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/jpeg')
    return jsonify({"success": False, "error": "No image found for this template"}), 404

@app.route('/api/templates/<name>/set_default', methods=['POST'])
def set_template_default(name):
    """make one template the default and unset all others"""
    conf_data = get_configs()
    found = False
    for b in conf_data.get("blueprints", []):
        if b["name"] == name:
            b["is_default"] = True
            found = True
        else:
            b["is_default"] = False
    if not found:
        return jsonify({"success": False, "error": f"Template '{name}' not found"}), 404
    save_configs(conf_data)
    return jsonify({"success": True, "message": f"'{name}' is now the default template."})

@app.route('/api/templates/<name>', methods=['DELETE'])
def delete_template(name):
    conf_data = get_configs()
    initial_count = len(conf_data.get("blueprints", []))
    
    deleted_bp = next((b for b in conf_data.get("blueprints", []) if b["name"] == name), None)
    if not deleted_bp:
        return jsonify({"success": False, "error": f"Template '{name}' not found"}), 404
        
    was_default = deleted_bp.get("is_default", False)
    
    conf_data["blueprints"] = [b for b in conf_data.get("blueprints", []) if b["name"] != name]
    
    # if the one we deleted was the default, make the first remaining one default
    if was_default and len(conf_data["blueprints"]) > 0:
        conf_data["blueprints"][0]["is_default"] = True
        
    save_configs(conf_data)
    return jsonify({"success": True, "message": f"Template '{name}' deleted."})

@app.route('/api/locate_anchors', methods=['POST'])
def locate_anchors():
    """takes an image, looks for the 4 corner markers, straightens the image, and sends it back"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
        
    file = request.files['file']
    raw_bytes = np.asarray(bytearray(file.read()), dtype=np.uint8)
    img_ref = cv2.imdecode(raw_bytes, 1)

    anchors = image_core.locate_anchor_points(img_ref)
    
    if len(anchors) == 4:
        warp_mat = image_core.calculate_warp_matrix(anchors, CANVAS_W, CANVAS_H)
        canvas = cv2.warpPerspective(img_ref, warp_mat, (CANVAS_W, CANVAS_H), flags=cv2.WARP_INVERSE_MAP)
        return send_file(img_to_bytes(canvas), mimetype='image/jpeg')
    else:
        # couldnt find corners so just resize the image and return it
        canvas = cv2.resize(img_ref, (CANVAS_W, CANVAS_H))
        return send_file(img_to_bytes(canvas), mimetype='image/jpeg', headers={'X-Error': 'Anchors not found'})

@app.route('/api/keys', methods=['GET'])
def list_keys():
    return jsonify(get_keys())

@app.route('/api/keys/scan', methods=['POST'])
def scan_answer_key():
    """reads an uploaded answer sheet, scans the bubbles, and sends back what answers it found"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
        
    file = request.files['file']
    eval_id = request.form.get("eval_id")
    sync_bp_name = request.form.get("blueprint")

    if not eval_id or not sync_bp_name:
        return jsonify({"success": False, "error": "Missing eval_id or blueprint"}), 400

    raw = np.asarray(bytearray(file.read()), dtype=np.uint8)
    img = cv2.imdecode(raw, 1)
    
    conf_data = get_configs()
    target_bp = next((b for b in conf_data.get("blueprints", []) if b["name"] == sync_bp_name), None)
    
    if not target_bp:
        return jsonify({"success": False, "error": "Blueprint not found"}), 404

    anchors = image_core.locate_anchor_points(img)
    if len(anchors) == 4:
        mat = image_core.calculate_warp_matrix(anchors, CANVAS_W, CANVAS_H)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        score, data_dump, graphics = region_scanner.evaluate_template_zones(
            gray, mat, target_bp.get("zones", []), {}
        )
        
        gt, rc, inf, track, blue_exp = graphics
        image_core.render_evaluation_graphics(img, gt, rc, inf, track, blue_exp)

        truth_matrix = {}
        
        for k, v in data_dump.items():
            if v["type"] == "Questions":
                for res in v["data"]:
                    qn = str(res["Question"])
                    ans = res["Given Answer"]
                    truth_matrix[qn] = ans

        import base64
        _, buffer = cv2.imencode('.jpg', img)
        img_b64 = base64.b64encode(buffer).decode("utf-8")

        return jsonify({
            "success": True, 
            "answers": truth_matrix, 
            "image_b64": img_b64,
            "total_answers": len(truth_matrix)
        })
    else:
        return jsonify({"success": False, "error": "Could not find the 4 corner markers on the page."}), 400

@app.route('/api/keys/save', methods=['POST'])
def save_answer_key():
    """takes the verified answers from the user and saves them to the json file"""
    data = request.json
    eval_id = data.get("eval_id")
    sync_bp_name = data.get("blueprint")
    truth_matrix = data.get("answers", {})
    subject_name = data.get("subject_name", "")
    exam_date = data.get("exam_date", "")
    passing_marks = data.get("passing_marks", 0)

    if not eval_id or not sync_bp_name:
        return jsonify({"success": False, "error": "Missing eval_id or blueprint"}), 400

    db = get_keys()
    
    # check if this key already exists so we update it instead of making a duplicate
    idx = next((i for i, k in enumerate(db.get("keys", [])) if k["eval_id"] == eval_id), -1)
    
    new_key = {
        "id": f"tm_{eval_id}",
        "blueprint": sync_bp_name,
        "eval_id": eval_id,
        "subject_name": subject_name,
        "exam_date": exam_date,
        "passing_marks": passing_marks,
        "answers": truth_matrix
    }

    if idx >= 0:
        db["keys"][idx] = new_key
    else:
        db.setdefault("keys", []).append(new_key)
        
    save_keys(db)
    return jsonify({"success": True, "message": "Answer Key Saved Successfully", "total_answers": len(truth_matrix)})

@app.route('/api/keys/<eval_id>', methods=['DELETE'])
def delete_answer_key(eval_id):
    db = get_keys()
    initial_count = len(db.get("keys", []))
    db["keys"] = [k for k in db.get("keys", []) if k["eval_id"] != eval_id]
    
    if len(db["keys"]) == initial_count:
        return jsonify({"success": False, "error": f"Answer key '{eval_id}' not found"}), 404
        
    save_keys(db)
    return jsonify({"success": True, "message": f"Answer key '{eval_id}' deleted."})

@app.route('/api/grade', methods=['POST'])
def grade_paper():
    """grades a student's paper by comparing their answers to the saved answer key"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
        
    file = request.files['file']
    eval_id = request.form.get("eval_id")

    if not eval_id:
        return jsonify({"success": False, "error": "Missing eval_id"}), 400

    raw = np.asarray(bytearray(file.read()), dtype=np.uint8)
    subj_img = cv2.imdecode(raw, 1)

    db = get_keys()
    active_key = next((k for k in db.get("keys", []) if k["eval_id"] == eval_id), None)
    
    if not active_key:
        return jsonify({"success": False, "error": "Answer key not found"}), 404

    TRUTH = active_key["answers"]
    
    conf = get_configs()
    active_bp = next((b for b in conf.get("blueprints", []) if b["name"] == active_key["blueprint"]), None)
    
    if not active_bp:
        return jsonify({"success": False, "error": "Template linked to this answer key was deleted."}), 404

    anchors = image_core.locate_anchor_points(subj_img)
    if len(anchors) == 4:
        mat = image_core.calculate_warp_matrix(anchors, CANVAS_W, CANVAS_H)
        gray = cv2.cvtColor(subj_img, cv2.COLOR_BGR2GRAY)
        
        score, data_dump, graphics = region_scanner.evaluate_template_zones(
            gray, mat, active_bp.get("zones", []), TRUTH
        )
        
        # save a clean copy of the original image before drawing anything on it
        import base64
        import uuid
        from datetime import datetime
        
        _, clean_buf = cv2.imencode('.jpg', subj_img)
        clean_image_b64 = base64.b64encode(clean_buf).decode("utf-8")
        
        gt, rc, inf, track, blue_exp = graphics
        image_core.render_evaluation_graphics(subj_img, gt, rc, inf, track, blue_exp)
        
        # draw the student info box on the image
        info_lines = []
        for k, v in data_dump.items():
            if v["type"] == "Info":
                info_lines.append(f"{v['label']}: {''.join(v['data'])}")
                
        if info_lines:
            # put a light background box behind the info text so it's readable
            overlay = subj_img.copy()
            box_w = 450
            box_h = 40 + len(info_lines) * 35
            cv2.rectangle(overlay, (20, 20), (20 + box_w, 20 + box_h), (250, 250, 250), -1)
            cv2.addWeighted(overlay, 0.85, subj_img, 0.15, 0, subj_img)
            cv2.rectangle(subj_img, (20, 20), (20 + box_w, 20 + box_h), (100, 100, 100), 2)
            
            # heading
            cv2.putText(subj_img, "STUDENT DATA", (35, 50), cv2.FONT_HERSHEY_DUPLEX, 0.7, (50, 50, 50), 2)
            
            for i, line in enumerate(info_lines):
                y_pos = 85 + i * 35
                cv2.putText(subj_img, line, (35, y_pos), cv2.FONT_HERSHEY_DUPLEX, 0.8, (10, 10, 10), 2)
                
        # draw the score on the top right corner
        sx = subj_img.shape[1] - 400
        sy = 30
        cv2.rectangle(subj_img, (sx, sy), (sx + 350, sy + 150), (30, 30, 30), -1)
        cv2.putText(subj_img, "TOTAL SCORE", (sx + 20, sy + 30), cv2.FONT_HERSHEY_DUPLEX, 0.7, (200, 200, 200), 1)
        cv2.putText(subj_img, f"{score}", (sx + 20, sy + 110), cv2.FONT_HERSHEY_DUPLEX, 2.5, (0, 255, 0), 3)
        cv2.putText(subj_img, f"/ {len(TRUTH)}", (sx + 150, sy + 110), cv2.FONT_HERSHEY_DUPLEX, 1.5, (150, 150, 150), 2)

        # get the student info fields like roll number, udise code etc
        student_info = {}
        for k, v in data_dump.items():
            if v["type"] == "Info":
                student_info[v['label']] = ''.join(v['data'])
        
        _, buffer = cv2.imencode('.jpg', subj_img)
        img_b64 = base64.b64encode(buffer).decode("utf-8")

        all_q = []
        for k, v in data_dump.items():
            if v["type"] == "Questions":
                all_q.extend(v["data"])
                
        if all_q:
            all_q.sort(key=lambda x: x["Question"])

        # save everything to a result record
        res_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        passing_marks = int(active_key.get("passing_marks", 0))
        
        record = {
            "id": res_id,
            "timestamp": timestamp,
            "eval_id": eval_id,
            "student_info": student_info,
            "score": score,
            "total": len(TRUTH),
            "passing_marks": passing_marks,
            "report": all_q,
            "image_b64": img_b64,
            "clean_image_b64": clean_image_b64
        }
        
        results_db = get_results()
        results_db.setdefault("results", []).append(record)
        save_results(results_db)

        return jsonify({
            "success": True, 
            "id": res_id,
            "student_info": student_info,
            "score": score, 
            "total": len(TRUTH), 
            "passing_marks": passing_marks,
            "report": all_q,
            "image_b64": img_b64
        })
    else:
        return jsonify({"success": False, "error": "Could not find the 4 corner markers on the page."}), 400

@app.route('/api/results', methods=['GET'])
def list_results():
    """sends back all the past grading results but without the big image data to keep it fast"""
    db = get_results()
    keys_db = get_keys()
    summary = []
    
    # make a quick lookup map so we dont have to search every time
    key_meta = {k["eval_id"]: {"subject_name": k.get("subject_name", ""), "exam_date": k.get("exam_date", "")} for k in keys_db.get("keys", [])}
    
    for r in db.get("results", []):
        meta = key_meta.get(r.get("eval_id", ""), {})
        summary.append({
            "id": r["id"],
            "timestamp": r.get("timestamp", ""),
            "eval_id": r.get("eval_id", ""),
            "subject_name": meta.get("subject_name", ""),
            "exam_date": meta.get("exam_date", ""),
            "student_info": r.get("student_info", {}),
            "score": r["score"],
            "total": r["total"],
            "passing_marks": r.get("passing_marks", 0),
            "report": r.get("report", []),
            "edited": r.get("edited", False)
        })
    # show newest results first
    summary.reverse()
    return jsonify({"results": summary})

@app.route('/api/results/<res_id>/image', methods=['GET'])
def get_result_image(res_id):
    """sends just the graded image for one result"""
    db = get_results()
    match = next((r for r in db.get("results", []) if r["id"] == res_id), None)
    if match and "image_b64" in match:
        return jsonify({"success": True, "image_b64": match["image_b64"]})
    return jsonify({"success": False, "error": "Not found"}), 404

@app.route('/api/results/<res_id>', methods=['GET'])
def get_result_detail(res_id):
    """sends the full result record including report and image for editing"""
    db = get_results()
    match = next((r for r in db.get("results", []) if r["id"] == res_id), None)
    if not match:
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({
        "success": True,
        "id": match["id"],
        "timestamp": match.get("timestamp", ""),
        "eval_id": match.get("eval_id", ""),
        "student_info": match.get("student_info", {}),
        "score": match["score"],
        "total": match["total"],
        "passing_marks": match.get("passing_marks", 0),
        "report": match.get("report", []),
        "image_b64": match.get("image_b64", "")
    })

@app.route('/api/results/<res_id>', methods=['DELETE'])
def delete_result(res_id):
    """removes a single grading result from the database"""
    db = get_results()
    results = db.get("results", [])
    before = len(results)
    db["results"] = [r for r in results if r["id"] != res_id]
    if len(db["results"]) == before:
        return jsonify({"success": False, "error": "Result not found"}), 404
    save_results(db)
    return jsonify({"success": True, "message": "Result deleted"})

@app.route('/api/grade/update', methods=['POST'])
def update_grade():
    """lets the user fix wrong answers and student info, recalculates score, and re-renders the graded image from scratch"""
    data = request.json
    res_id = data.get("result_id")
    corrected_answers = data.get("answers", {})
    updated_student_info = data.get("student_info", None)

    if not res_id:
        return jsonify({"success": False, "error": "Missing result_id"}), 400

    results_db = get_results()
    target_result = next((r for r in results_db.get("results", []) if r["id"] == res_id), None)

    if not target_result:
        return jsonify({"success": False, "error": "Result not found"}), 404

    keys_db = get_keys()
    active_key = next((k for k in keys_db.get("keys", []) if k["eval_id"] == target_result["eval_id"]), None)

    if not active_key:
        return jsonify({"success": False, "error": "Original Answer Key no longer exists"}), 404

    TRUTH = active_key["answers"]

    # update student info if the user edited it
    if updated_student_info is not None:
        target_result["student_info"] = updated_student_info

    # go through each question and check again with the corrected answers
    new_score = 0
    updated_report = []
    
    for old_item in target_result.get("report", []):
        qn = str(old_item["Question"])
        
        # use the corrected answer if user changed it, otherwise keep the old one
        given = corrected_answers.get(qn, old_item["Given Answer"])
        is_correct = 1 if given == TRUTH.get(qn) else 0
        new_score += is_correct

        updated_report.append({
            "Question": old_item["Question"],
            "Given Answer": given,
            "Correct Answer": TRUTH.get(qn, ""),
            "Mark": is_correct
        })

    target_result["score"] = new_score
    target_result["report"] = updated_report
    target_result["edited"] = True

    # re-render the graded image using the teacher's corrected data
    import base64
    new_image_b64 = None
    
    # get the template blueprint so we know where bubbles are
    conf = get_configs()
    active_bp = next((b for b in conf.get("blueprints", []) if b["name"] == active_key["blueprint"]), None)
    
    # use the clean image to start fresh
    source_b64 = target_result.get("clean_image_b64") or target_result.get("image_b64")
    
    # build a quick lookup of the teacher's corrected answers from the report
    corrected_given = {}
    for item in updated_report:
        corrected_given[str(item["Question"])] = {
            "given": item["Given Answer"],
            "correct": item["Correct Answer"],
            "mark": item["Mark"]
        }
    
    if source_b64 and active_bp:
        try:
            img_bytes = base64.b64decode(source_b64)
            img_array = np.asarray(bytearray(img_bytes), dtype=np.uint8)
            subj_img = cv2.imdecode(img_array, 1)
            
            if subj_img is not None:
                anchors = image_core.locate_anchor_points(subj_img)
                
                green_ticks = []
                red_crosses = []
                info_fills = []
                track_boxes = []
                blue_expected = []
                
                if len(anchors) == 4:
                    mat = image_core.calculate_warp_matrix(anchors, CANVAS_W, CANVAS_H)
                    
                    # go through each zone and draw marks based on what the teacher said
                    for zone in active_bp.get("zones", []):
                        kind = zone.get("type", "questions")
                        count = zone.get("num_items", 1)
                        choices = zone.get("num_options", 4)
                        labels = zone.get("options_list", ["A", "B", "C", "D"])
                        orientation = zone.get("direction", "vertical")
                        q_start = zone.get("start_q", 1)
                        tag = zone.get("info_label", "")
                        
                        zx = zone.get("x", 0)
                        zy = zone.get("y", 0)
                        zw = zone.get("width", 100)
                        zh = zone.get("height", 100)
                        ox = zone.get("offset_x", 0.0)
                        oy = zone.get("offset_y", 0.0)
                        br = zone.get("bubble_radius", 10)
                        
                        # calculate bubble positions same way as region_scanner does
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
                                
                                (ax, ay) = image_core.project_pixel(mat, vcx, vcy)
                                (ex, ey) = image_core.project_pixel(mat, vcx + br, vcy)
                                calc_rad = int(np.sqrt((ex - ax)**2 + (ey - ay)**2))
                                calc_rad = max(5, min(calc_rad, 35))
                                
                                row_points.append((int(ax), int(ay), calc_rad))
                            mesh.append(row_points)
                        
                        if kind == "questions":
                            # for each question row, use the teacher's corrected answer
                            for i in range(count):
                                qn = str(q_start + i)
                                entry = corrected_given.get(qn)
                                
                                # draw tracking boxes for all bubbles in this row
                                for j in range(choices):
                                    fx, fy, r = mesh[i][j]
                                    track_boxes.append((int(fx), int(fy), int(r), (150, 150, 150)))
                                
                                if entry:
                                    given_val = entry["given"]
                                    is_correct = entry["mark"] == 1
                                    
                                    # find which bubble column matches the given answer
                                    picked_idx = -1
                                    if given_val and given_val != "-":
                                        if given_val in labels:
                                            picked_idx = labels.index(given_val)
                                    
                                    if picked_idx >= 0 and picked_idx < len(mesh[i]):
                                        bx, by, brd = mesh[i][picked_idx]
                                        if is_correct:
                                            green_ticks.append((int(bx), int(by), int(brd)))
                                        else:
                                            red_crosses.append((int(bx), int(by), int(brd)))
                                            # also mark the correct answer in blue
                                            correct_val = entry["correct"]
                                            if correct_val and correct_val in labels:
                                                c_idx = labels.index(correct_val)
                                                if c_idx < len(mesh[i]):
                                                    cx, cy, cr = mesh[i][c_idx]
                                                    blue_expected.append((int(cx), int(cy), int(cr)))
                        
                        elif kind == "info":
                            # for info zones, highlight the detected/corrected values
                            student_info = target_result.get("student_info", {})
                            info_value = student_info.get(tag, "")
                            
                            for i in range(count):
                                char = info_value[i] if i < len(info_value) else ""
                                
                                # draw tracking boxes
                                for j in range(choices):
                                    fx, fy, r = mesh[i][j]
                                    track_boxes.append((int(fx), int(fy), int(r), (150, 150, 150)))
                                
                                # find which bubble matches this character
                                if char and char in labels:
                                    picked_idx = labels.index(char)
                                    if picked_idx < len(mesh[i]):
                                        wx, wy, wr = mesh[i][picked_idx]
                                        info_fills.append((int(wx), int(wy), int(wr), (200, 100, 50)))
                
                # draw all the marks on the image
                image_core.render_evaluation_graphics(subj_img, green_ticks, red_crosses, info_fills, track_boxes, blue_expected)
                
                # draw the student info box on top
                student_info = target_result.get("student_info", {})
                info_lines = [f"{k}: {v}" for k, v in student_info.items() if v]
                
                if info_lines:
                    overlay = subj_img.copy()
                    box_w = 450
                    box_h = 40 + len(info_lines) * 35
                    cv2.rectangle(overlay, (20, 20), (20 + box_w, 20 + box_h), (250, 250, 250), -1)
                    cv2.addWeighted(overlay, 0.85, subj_img, 0.15, 0, subj_img)
                    cv2.rectangle(subj_img, (20, 20), (20 + box_w, 20 + box_h), (100, 100, 100), 2)
                    cv2.putText(subj_img, "STUDENT DATA", (35, 50), cv2.FONT_HERSHEY_DUPLEX, 0.7, (50, 50, 50), 2)
                    for i, line in enumerate(info_lines):
                        y_pos = 85 + i * 35
                        cv2.putText(subj_img, line, (35, y_pos), cv2.FONT_HERSHEY_DUPLEX, 0.8, (10, 10, 10), 2)
                
                # draw the updated score box
                sx = subj_img.shape[1] - 400
                sy = 30
                cv2.rectangle(subj_img, (sx, sy), (sx + 350, sy + 150), (30, 30, 30), -1)
                cv2.putText(subj_img, "TOTAL SCORE", (sx + 20, sy + 30), cv2.FONT_HERSHEY_DUPLEX, 0.7, (200, 200, 200), 1)
                cv2.putText(subj_img, f"{new_score}", (sx + 20, sy + 110), cv2.FONT_HERSHEY_DUPLEX, 2.5, (0, 255, 0), 3)
                cv2.putText(subj_img, f"/ {len(TRUTH)}", (sx + 150, sy + 110), cv2.FONT_HERSHEY_DUPLEX, 1.5, (150, 150, 150), 2)
                
                # encode the new image
                _, buffer = cv2.imencode('.jpg', subj_img)
                new_image_b64 = base64.b64encode(buffer).decode("utf-8")
                target_result["image_b64"] = new_image_b64
        except Exception as e:
            print(f"could not re-render image: {e}")

    save_results(results_db)

    response = {
        "success": True,
        "message": "Grade updated successfully.",
        "score": new_score,
        "total": target_result["total"],
        "passing_marks": target_result.get("passing_marks", 0),
        "report": updated_report,
        "student_info": target_result.get("student_info", {})
    }
    
    if new_image_b64:
        response["image_b64"] = new_image_b64
    
    return jsonify(response)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
