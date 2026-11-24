import json
from pathlib import Path

BASE_PATH = Path("data")

def init_course_dir(course_id):
    course_path = BASE_PATH / f"course_{course_id}"
    subdirs = ["forums", "videos", "webpagetext", "links", "image", "resources", "document", "glossaries", "quizzes"]
    
    for sub in subdirs:
        (course_path / sub).mkdir(parents=True, exist_ok=True)
    
    return course_path

def save_json(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def save_binary_file(content, path):
    with open(path, "wb") as f:
        f.write(content)
