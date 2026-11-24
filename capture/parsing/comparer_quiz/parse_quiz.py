import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Any
from pathlib import Path
import html


def strip_html(text: str) -> str:
    if not text:
        return ""
    
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def extract_cdata(element) -> str:
    if element is None:
        return ""
    
    text = element.text or ""
    
    if text.strip().startswith('<![CDATA['):
        text = text.replace('<![CDATA[', '').replace(']]>', '')
    
    return text.strip()


def parse_quiz_xml(xml_file: Path) -> List[Dict[str, Any]]:
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    questions = []
    
    for question_elem in root.findall('.//question'):
        question_type = question_elem.get('type')
        
        if question_type == 'category':
            continue
        
        question_id = None
        for node in question_elem.iter():
            if isinstance(node, ET.Element):
                pass
        
        name_elem = question_elem.find('name/text')
        question_name = name_elem.text if name_elem is not None else "Unnamed"
        
        questiontext_elem = question_elem.find('questiontext[@format="html"]/text')
        question_text_raw = extract_cdata(questiontext_elem) if questiontext_elem is not None else ""
        question_text = strip_html(question_text_raw)
        
        idnumber_elem = question_elem.find('idnumber')
        if idnumber_elem is not None and idnumber_elem.text:
            question_id = idnumber_elem.text
        else:
            import hashlib
            question_id = hashlib.md5(question_name.encode()).hexdigest()[:12]
        
        answers = []
        for answer_elem in question_elem.findall('.//answer/text'):
            answer_text = answer_elem.text if answer_elem is not None else ""
            if answer_text.strip():
                answers.append(answer_text.strip())
        
        question_data = {
            'question_id': question_id,
            'question_name': question_name,
            'question_text': question_text,
            'question_type': question_type,
            'answers': answers,
            'metadata': {
                'source_file': str(xml_file),
                'parse_timestamp': None 
            }
        }
        
        questions.append(question_data)
    
    return questions


def extract_version_from_filename(filename: str) -> str:
    match = re.search(r'\[([^\]]+)\]', filename)
    if match:
        version = match.group(1)
        # Normalize: "WiSe 202526" -> "WiSe 2025/26"
        if re.match(r'WiSe \d{6}', version):
            year_str = version.split()[1]
            version = f"WiSe {year_str[:4]}/{year_str[4:]}"
        return version
    return "Unknown"


if __name__ == "__main__":
    quiz_dir = Path(__file__).resolve().parents[3] / "data" / "comparison" / "quiz"
    
    for xml_file in sorted(quiz_dir.glob("*.xml")):
        print(f"File: {xml_file.name}")
        version = extract_version_from_filename(xml_file.name)
        print(f"Version: {version}")
        
        questions = parse_quiz_xml(xml_file)
        print(f"{len(questions)} questions\n")
        
        for i, q in enumerate(questions, 1):
            print(f"{i}. {q['question_name']} ({q['question_type']})")
            print(f"   ID: {q['question_id']}")
            print(f"   Text: {q['question_text'][:100]}...")
            if q['answers']:
                print(f"   Answers: {', '.join(q['answers'][:3])}{'...' if len(q['answers']) > 3 else ''}")
            print()