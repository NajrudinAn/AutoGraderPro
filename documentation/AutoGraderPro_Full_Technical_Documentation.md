# AutoGraderPro Full Technical Documentation

## 1. Project Overview

AutoGraderPro is an end-to-end OMR (Optical Mark Recognition) grading system designed for school exam workflows. It supports:

- Template setup (where bubbles are located on a sheet)
- Answer key extraction from a teacher-filled sheet
- Student paper grading (single or multiple)
- Result storage and post-grading correction/editing
- Batch/offline grading pipeline for folder-based processing

The system is file-backed (JSON storage), image-driven (OpenCV), and UI-enabled through a standalone HTML + JavaScript frontend.

## 2. High-Level Architecture

The project is split into three execution layers and one shared CV core:

- API backend: [api_server.py](api_server.py)
- Frontend app: [frontend_app.html](frontend_app.html)
- Batch processor: [batch_process.py](batch_process.py)
- Core CV + scoring modules:
  - [src/image_core.py](src/image_core.py)
  - [src/region_scanner.py](src/region_scanner.py)

### 2.1 Data Storage Files

Persistent state is stored in JSON files at project root:

- Template blueprints: [configs.json](configs.json)
- Answer keys: [master_answers.json](master_answers.json)
- Grading history: [graded_results.json](graded_results.json)

Image assets are stored in:

- Template images folder: [data](data)

Input sample scans for batch mode:

- Batch input folder: [input_docs](input_docs)

## 3. Core Runtime Components

## 3.1 Flask API Server ([api_server.py](api_server.py))

Main backend service running on port 5002. It handles:

- CRUD for templates, answer keys, and results
- Anchor detection and image alignment endpoint
- Answer key scan endpoint
- Student grading endpoint
- Grade correction/re-render endpoint

### Important constants

- Virtual canvas width: 1000
- Virtual canvas height: 1400

These constants define the normalized sheet coordinate system used for template zones and warping logic.

### Internal helper responsibilities

- Read/write JSON storage with safe fallback defaults
- Convert OpenCV image arrays to memory bytes for HTTP image responses
- Keep backward compatibility when fetching template image by old naming convention

## 3.2 Frontend Web Application ([frontend_app.html](frontend_app.html))

A single-page browser interface with 4 primary tabs:

- Template Setup
- Upload Answer Key
- Grade Papers
- View Results

Frontend behavior is plain JavaScript (no build system), with Tailwind loaded from CDN.

It provides:

- Visual crop/box editor for zone creation
- Lens zoom and draggable/resizable selection tools
- Answer key preview and manual correction before save
- Multi-file grading in one run
- Inline result editing (answers and student info)
- CSV export with dynamic columns

## 3.3 Batch Processor ([batch_process.py](batch_process.py))

Headless local pipeline for processing all images from folder input. It:

- Loads active blueprint and key from JSON
- Iterates files from input_docs
- Grades each image using same CV/scoring engine
- Saves annotated images and per-file CSV reports in processed_exports

This script bypasses HTTP and is useful for bulk/offline workflows.

## 3.4 Computer Vision and Scoring Core

### [src/image_core.py](src/image_core.py)

Responsible for geometry and rendering:

- locate_anchor_points:
  - Converts image to grayscale
  - Adaptive thresholding to isolate dark markers
  - Finds contours above minimum area
  - Picks nearest detected marker to each sheet corner
- sort_coordinates:
  - Orders 4 anchor points into consistent corner ordering
- calculate_warp_matrix:
  - Builds perspective transform from virtual canvas to real image
- project_pixel:
  - Projects a virtual point into actual image coordinates
- auto_center_marker:
  - Optional local centering refinement around expected bubble center
- render_evaluation_graphics:
  - Draws overlays for tracking boxes, correct/incorrect marks, info bubbles, and expected-correct hints

### [src/region_scanner.py](src/region_scanner.py)

Responsible for bubble reading and scoring:

- _calculate_ink_weight:
  - Local patch extraction
  - CLAHE contrast enhancement
  - Gaussian blur + adaptive threshold
  - Circular mask pixel density measurement
- _search_for_nearby_blob:
  - Local contour search to adjust bubble center offsets
- evaluate_template_zones:
  - Builds expected bubble mesh from zone configuration
  - Applies local alignment offsets per zone/row
  - Measures per-choice density
  - Picks strongest choice with threshold gate
  - Compares picked choice to truth answer for question zones
  - Collects visualization primitives and structured output data

The scanner returns:

- Total score
- Structured parsed data for questions/info zones
- Drawing payload for annotation rendering

## 4. Data Model Details

## 4.1 Template Blueprint Model ([configs.json](configs.json))

A blueprint contains:

- name
- is_default
- zones array
- optional image_file

Each zone contains:

- id: unique zone key
- type: questions or info
- direction: vertical or horizontal
- num_items: rows/items
- num_options: choices per item
- options_list: labels (for example A,B,C,D or 0-9)
- start_q: first question index for question zones
- info_label: field label for info zones (for example Roll No)
- x, y, width, height: zone rectangle in virtual canvas coordinates
- offset_x, offset_y: calibration offsets
- bubble_radius

## 4.2 Answer Key Model ([master_answers.json](master_answers.json))

Each answer key record contains:

- id (prefixed with tm_)
- blueprint (template name)
- eval_id (public grading key id)
- subject_name
- exam_date
- passing_marks
- answers map: question number string to option label

## 4.3 Result Model ([graded_results.json](graded_results.json))

Each stored grading result contains:

- id
- timestamp
- eval_id
- student_info object
- score
- total
- passing_marks
- report array of per-question objects
- image_b64 (annotated image)
- clean_image_b64 (pre-annotation image, used for re-rendering after edits)
- edited flag (after manual correction)

## 5. API Contract and Feature Flow

## 5.1 Template APIs

- GET /api/templates
  - Returns all blueprints
- POST /api/templates
  - Creates or updates template by name
  - Optional default switch logic unsets default on others
- POST /api/templates/{name}/image
  - Uploads and links template image file to template
  - Deletes previous linked template image if present
- GET /api/templates/{name}/image
  - Returns template image if found
  - Supports fallback old naming convention
- POST /api/templates/{name}/set_default
  - Sets one template default and unsets others
- DELETE /api/templates/{name}
  - Deletes template
  - If default deleted, first remaining template becomes default

## 5.2 Anchor and Preprocessing API

- POST /api/locate_anchors
  - Input image
  - Detects corner anchors
  - If found: returns perspective-corrected canvas image
  - If not found: returns resized image with error header

## 5.3 Answer Key APIs

- GET /api/keys
  - Returns all keys
- POST /api/keys/scan
  - Reads teacher-filled answer sheet
  - Requires eval_id and blueprint
  - Runs full scanner with empty truth dict
  - Extracts detected answers and preview image
- POST /api/keys/save
  - Saves reviewed answer matrix and metadata
  - Upsert by eval_id
- DELETE /api/keys/{eval_id}
  - Deletes key by eval_id

## 5.4 Grading APIs

- POST /api/grade
  - Inputs student sheet and eval_id
  - Loads matching answer key and its blueprint
  - Runs scanning and scoring
  - Builds student info object from info zones
  - Renders rich graded image (ticks/crosses/expected/score/info panel)
  - Persists result to graded_results
  - Returns score report and image_b64

- POST /api/grade/update
  - Accepts corrected answers and edited student info
  - Recomputes score from corrected data
  - Re-renders image from clean_image_b64 where possible
  - Updates stored report, score, edited flag, and image

## 5.5 Results APIs

- GET /api/results
  - Returns lightweight list (metadata without base64 payload)
  - Joins subject/exam metadata from answer key DB
  - Returns newest first
- GET /api/results/{res_id}/image
  - Returns graded image only
- GET /api/results/{res_id}
  - Returns full result record for detail/edit screen
- DELETE /api/results/{res_id}
  - Removes result permanently

## 6. End-to-End Functional Workflow

## 6.1 Template Setup Flow

1. User uploads blank sheet in frontend.
2. Frontend sends image to /api/locate_anchors for normalized view.
3. User draws one or more zones with metadata (question/info).
4. Frontend submits template via /api/templates.
5. Frontend optionally uploads associated template image via /api/templates/{name}/image.

Output: persistent blueprint with geometry and labels.

## 6.2 Answer Key Generation Flow

1. User picks template and metadata (subject/date/passing marks).
2. User uploads teacher-filled answer sheet.
3. Frontend calls /api/keys/scan.
4. Backend scans all question zones and returns detected matrix + preview image.
5. User fixes any uncertain values in UI.
6. Frontend saves via /api/keys/save.

Output: reusable truth matrix keyed by eval_id.

## 6.3 Student Grading Flow

1. User selects eval_id (answer key) and uploads one or multiple student sheets.
2. For each file frontend calls /api/grade.
3. Backend:
   - Locates anchors
   - Computes warp matrix
   - Reads all zones
   - Scores against truth
   - Builds report and student info
   - Renders final annotated image
   - Saves result record
4. Frontend shows accordion cards with score and full per-question detail.

Output: saved result + immediate visual and tabular feedback.

## 6.4 Post-Grading Correction Flow

1. User opens a graded result in grade accordion or history modal.
2. User edits given answers and/or student info.
3. Frontend sends /api/grade/update.
4. Backend recalculates marks and regenerates annotation image using clean source.
5. Updated result is persisted with edited=true.

Output: corrected score history and revised graded image.

## 6.5 History and Export Flow

1. Frontend loads /api/results.
2. User filters by key, text, and percentage bounds.
3. UI computes summary statistics (pass/fail/avg).
4. User exports CSV from currently filtered set.

Output: portable result sheet including dynamic student info columns and per-question details.

## 7. Grading Engine Internals (Detailed)

## 7.1 Coordinate and Warping Strategy

- Templates are defined on a virtual plane of 1000x1400.
- During grading, each virtual bubble center is projected onto actual image through perspective matrix.
- This allows one template to work with captured sheets that have perspective distortion.

## 7.2 Bubble Localization Refinement

- The engine does not rely only on ideal projected centers.
- It searches nearby blobs around each expected location.
- It computes median shifts per row/zone and applies correction to improve robustness.

## 7.3 Bubble Fill Decision

For each candidate bubble:

- Extract local patch around center
- Boost contrast with CLAHE
- Apply adaptive threshold
- Count ink pixels inside circular mask
- Compute density ratio

Selection rule:

- Highest density candidate is top pick
- Must pass threshold gate
- Info zones use more lenient gate in some conditions

## 7.4 Scoring Rule

For question zones:

- Convert truth answer to label index
- Compare detected index with truth index
- If equal and valid, mark 1, else 0
- Total score is sum of marks

For info zones:

- Keep selected label sequence as textual field data

## 7.5 Visualization Semantics

The renderer uses color coding:

- Green: correct selected answer
- Red: incorrect selected answer
- Blue: expected correct bubble when wrong
- Gray rectangle: tracking area around each bubble
- Orange/brown style fill: info field selection

This allows quick human validation and easier correction.

## 8. Frontend Core Functionality Breakdown

## 8.1 Template Editor

- Draw, move, and resize selection box
- Preview generated bubble mesh before commit
- Save multiple zones and re-edit individual zones
- Set default template and manage templates list

## 8.2 Answer Key Screen

- Scan answer key image
- Display scanned image and editable answer list
- Highlight blanks and enforce completion before final save
- Manage and inspect saved keys

## 8.3 Grading Screen

- Supports multi-file upload
- Calls grading endpoint for each file
- Shows live progress status
- Displays graded result accordion with pass/fail/blank logic
- Inline edit and save back to backend

## 8.4 Results Screen

- Loads historical records
- Search and filter (student info, key, percentage range)
- Shows computed stats tiles
- Opens detail modal for view/edit
- Exports filtered data as CSV

## 9. Batch Processor Behavior (Detailed)

The batch script:

- Validates existence of blueprint and truth data before processing
- Fails with visual diagnostic window if initial config missing
- Iterates all images in input folder by extension
- Processes each using same core scanner and renderer
- Saves:
  - annotated image file
  - report CSV containing info fields and question-by-question mark status

This provides a non-UI processing option for high-volume scenarios.

## 10. Strengths of Current Design

- Clear separation of UI, API, and CV logic
- Reusable core scanner across web and batch modes
- Editable post-grading correction pipeline
- Persistent history and auditability
- Template image linkage and default template controls

## 11. Operational Notes and Constraints

- Anchor markers must be visible and sufficiently dark for reliable detection.
- Bubble fill detection assumes one dominant mark per item.
- JSON files are direct storage; concurrent multi-user writes may conflict.
- Base64 image storage can make graded_results.json very large over time.
- API has minimal authentication/authorization, intended for trusted local/institution deployment.

## 12. Dependency Stack

From [requirements.txt](requirements.txt):

- flask
- flask-cors
- opencv-python
- numpy
- streamlit
- pandas
- Pillow

Note: Streamlit-related assets remain in repository for legacy/utility paths, while primary flow is Flask + standalone frontend.

## 13. Practical Execution Guide

## 13.1 Web mode

1. Run backend: python api_server.py
2. Open [frontend_app.html](frontend_app.html) in browser
3. Configure template, scan key, and grade papers

## 13.2 Batch mode

1. Ensure template and answer key already exist in JSON
2. Put student images in [input_docs](input_docs)
3. Run: python batch_process.py
4. Collect outputs from processed_exports

## 14. Summary of Core Functionality

AutoGraderPro works by mapping template-defined bubble coordinates from a normalized virtual sheet to real camera-captured images using anchor-based perspective transformation, then reading bubble ink density with local enhancement and adaptive thresholding. It compares extracted student responses with a stored truth matrix, generates per-question grading, produces annotated evidence images, stores historical records, and supports manual correction with full score recomputation and image re-rendering.

This gives a complete lifecycle from template calibration to final score analytics for OMR exams.
