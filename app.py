from flask import Flask, render_template, send_from_directory, request, jsonify, redirect, url_for, session
from pathlib import Path
import random
import pandas as pd
import sqlite3
import json
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

# =========================================================
# CONFIG — tweak these as needed
# =========================================================

FLASH_DURATION_MS   = 2000
BLANK_DURATION_MS   = 300
RESPONSE_TIMEOUT_MS = 10000

TOTAL_ROUNDS   = 3   # 1 practice + 2 real
PRACTICE_ROUND = 0

random.seed(909)

base_path       = Path("static/Images_Folder")
original_folder = Path("static/Non_Postioned_Images_Folder")

# =========================================================
# DATABASE
# =========================================================

def init_db():
    conn = sqlite3.connect("experiment.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trials (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT,
            participant_id   TEXT,   -- MTurk workerId
            assignment_id    TEXT,   -- MTurk assignmentId
            hit_id           TEXT,   -- MTurk hitId
            trial_type       TEXT,   -- 'practice' or 'real'
            round_number     INTEGER,
            focus_category   TEXT,
            flashed_images   TEXT,
            final_options    TEXT,
            target_image     TEXT,
            selected_image   TEXT,
            is_correct       INTEGER,
            response_time_ms REAL,
            timed_out        INTEGER,
            metadata         TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# =========================================================
# EXCEL LOADER
# =========================================================

def convert_to_df(category):
    df = pd.read_excel("categories_images.xlsx", sheet_name=category)
    df = df.drop(columns=["Unnamed: 1"])
    df = df.drop(df.index[9:])
    df.set_index("Position", inplace=True)
    return df

# =========================================================
# TRIAL BUILDER
# =========================================================

def build_trial():
    categories       = [item.name for item in base_path.iterdir() if item.is_dir()]
    focus_category1  = random.sample(categories, 2)[0]
    Category_1       = [item.name for item in (base_path / focus_category1).iterdir() if item.is_dir()]
    shown_images     = random.sample(Category_1, 5)

    Positions = [
        'Top Left', 'Mid Left', 'Bottom Left',
        'Top Mid',  'Mid Mid',  'Bottom Mid',
        'Top Right','Mid Right','Bottom Right'
    ]

    focus_df   = convert_to_df(focus_category1)
    pairings   = list(zip(random.sample(Positions, 5), shown_images))

    path1 = base_path / focus_category1
    flash_paths = []
    for position, folder in pairings:
        img  = focus_df.loc[position, folder]
        full = str(path1 / folder / img).replace("\\", "/")
        flash_paths.append(full.removeprefix("static/"))

    selected_1    = random.choice(pairings)
    target_folder = selected_1[1]
    unshown       = [p for p in Category_1 if p not in shown_images]
    option_folders = random.sample(unshown, 2) + [target_folder]
    random.shuffle(option_folders)

    def _path(folder):
        full = str(original_folder / focus_category1 / f"{folder}.jpeg").replace("\\", "/")
        return full.removeprefix("static/")

    return {
        "focus_category": focus_category1,
        "flash_paths":    flash_paths,
        "option_paths":   [_path(f) for f in option_folders],
        "target_path":    _path(target_folder),
    }

# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def home():
    """
    Entry point. Two modes:
      - MTurk mode:  workerId, assignmentId, hitId, turkSubmitTo come in as
                     query params. We skip the registration form and go straight
                     to the experiment.
      - Direct mode: no MTurk params → show the manual registration form.
    """
    worker_id     = request.args.get("workerId", "").strip()
    assignment_id = request.args.get("assignmentId", "").strip()
    hit_id        = request.args.get("hitId", "").strip()
    turk_submit   = request.args.get("turkSubmitTo", "").strip()

    # MTurk sends assignmentId = "ASSIGNMENT_ID_NOT_AVAILABLE" when a worker
    # is previewing the HIT but hasn't accepted it yet.
    if assignment_id == "ASSIGNMENT_ID_NOT_AVAILABLE":
        return render_template("index.html", page="preview")

    if worker_id and assignment_id:
        # MTurk mode — bootstrap the session directly
        session["participant_id"] = worker_id
        session["assignment_id"]  = assignment_id
        session["hit_id"]         = hit_id
        session["turk_submit"]    = turk_submit
        session["round"]          = 0
        return redirect(url_for("trial"))

    # Direct / manual mode
    return render_template("index.html", page="register")


@app.route("/start", methods=["POST"])
def start():
    """Manual registration (non-MTurk)."""
    participant_id = request.form.get("participant_id", "").strip()
    if not participant_id:
        return redirect(url_for("home"))
    session["participant_id"] = participant_id
    session["assignment_id"]  = "manual"
    session["hit_id"]         = "manual"
    session["turk_submit"]    = ""
    session["round"]          = 0
    return redirect(url_for("trial"))


@app.route("/trial")
def trial():
    if "participant_id" not in session:
        return redirect(url_for("home"))

    current_round = session.get("round", 0)

    if current_round >= TOTAL_ROUNDS:
        return render_template(
            "index.html",
            page="done",
            participant_id=session["participant_id"],
            assignment_id=session.get("assignment_id", ""),
            turk_submit=session.get("turk_submit", ""),
        )

    is_practice = (current_round == PRACTICE_ROUND)
    trial_data  = build_trial()

    return render_template(
        "index.html",
        page="trial",
        participant_id=session["participant_id"],
        assignment_id=session.get("assignment_id", ""),
        hit_id=session.get("hit_id", ""),
        turk_submit=session.get("turk_submit", ""),
        round_number=current_round,
        trial_type="practice" if is_practice else "real",
        is_practice=is_practice,
        real_round=None if is_practice else current_round,
        total_real=TOTAL_ROUNDS - 1,
        flash_paths=trial_data["flash_paths"],
        option_paths=trial_data["option_paths"],
        target_path=trial_data["target_path"],
        focus_category=trial_data["focus_category"],
        flash_duration=FLASH_DURATION_MS,
        blank_duration=BLANK_DURATION_MS,
        response_timeout=RESPONSE_TIMEOUT_MS,
    )


@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()

    participant_id   = data.get("participant_id", "unknown")
    assignment_id    = data.get("assignment_id", "")
    hit_id           = data.get("hit_id", "")
    round_number     = data.get("round_number", 0)
    trial_type       = data.get("trial_type", "real")
    focus_category   = data.get("focus_category", "")
    flashed_images   = data.get("flashed_images", [])
    final_options    = data.get("final_options", [])
    target_image     = data.get("target_image", "")
    selected_image   = data.get("selected_image", "")
    response_time_ms = data.get("response_time_ms", None)
    timed_out        = 1 if response_time_ms is None else 0
    is_correct       = 1 if selected_image == target_image else 0

    conn = sqlite3.connect("experiment.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO trials (
            timestamp, participant_id, assignment_id, hit_id,
            trial_type, round_number, focus_category,
            flashed_images, final_options, target_image,
            selected_image, is_correct, response_time_ms, timed_out, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        participant_id, assignment_id, hit_id,
        trial_type, round_number, focus_category,
        json.dumps(flashed_images), json.dumps(final_options),
        target_image, selected_image,
        is_correct, response_time_ms, timed_out,
        json.dumps(data.get("metadata", {})),
    ))
    conn.commit()
    conn.close()

    if "round" in session:
        session["round"] = session["round"] + 1

    return jsonify({"status": "success", "is_correct": bool(is_correct)})


@app.route('/Images_Folder/<path:filename>')
def serve_images(filename):
    return send_from_directory('static/Images_Folder', filename)

@app.route('/Non_Postioned_Images_Folder/<path:filename>')
def serve_nonpositioned(filename):
    return send_from_directory('static/Non_Postioned_Images_Folder', filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
