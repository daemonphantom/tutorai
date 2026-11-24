from typing import List, Dict, Any
from difflib import SequenceMatcher
import json
from pathlib import Path


def calculate_similarity(text1: str, text2: str) -> float:
    return SequenceMatcher(None, text1, text2).ratio()


def find_text_diff(old_text: str, new_text: str) -> str:
    if old_text == new_text:
        return "No changes"
    
    matcher = SequenceMatcher(None, old_text, new_text)
    opcodes = matcher.get_opcodes()
    
    changes = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'replace':
            changes.append(f"'{old_text[i1:i2]}' -> '{new_text[j1:j2]}'")
        elif tag == 'delete':
            changes.append(f"Removed: '{old_text[i1:i2]}'")
        elif tag == 'insert':
            changes.append(f"Added: '{new_text[j1:j2]}'")
    
    if not changes:
        return "Minor text changes"
    
    if len(changes) > 3:
        return ", ".join(changes[:3]) + f" (and {len(changes) - 3} more changes)"
    
    return ", ".join(changes)


def normalize_question_key(question_name: str) -> str:
    return question_name.strip().lower()


def compare_questions(q1: Dict[str, Any], q2: Dict[str, Any]) -> Dict[str, Any]:
    text_similarity = calculate_similarity(q1['question_text'], q2['question_text'])
    
    old_answers = ' | '.join(q1.get('answers', []))
    new_answers = ' | '.join(q2.get('answers', []))
    answer_similarity = calculate_similarity(old_answers, new_answers)
    
    overall_similarity = (text_similarity + answer_similarity) / 2
    
    if q1['question_text'] == q2['question_text'] and old_answers == new_answers:
        change_type = 'unchanged'
        text_diff = "No changes"
    else:
        change_type = 'modified'
        changes = []
        
        if q1['question_text'] != q2['question_text']:
            diff_description = find_text_diff(q1['question_text'], q2['question_text'])
            changes.append(f"Question: {diff_description}")
        
        if old_answers != new_answers:
            old_ans_list = q1.get('answers', [])
            new_ans_list = q2.get('answers', [])
            
            if len(old_ans_list) != len(new_ans_list):
                changes.append(f"Answers count: {len(old_ans_list)} → {len(new_ans_list)}")
            else:
                for i, (old_a, new_a) in enumerate(zip(old_ans_list, new_ans_list, strict=False)):
                    if old_a != new_a:
                        changes.append(f"Answer {i+1}: '{old_a}' → '{new_a}'")
        
        text_diff = "; ".join(changes) if changes else "Modified"
    
    return {
        'change_type': change_type,
        'similarity': overall_similarity,
        'text_diff': text_diff,
        'text_similarity': text_similarity,
        'answer_similarity': answer_similarity,
        'old_question': q1,
        'new_question': q2
    }


def compare_quiz_versions(old_questions: List[Dict[str, Any]], new_questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    old_map = {normalize_question_key(q['question_name']): q for q in old_questions}
    new_map = {normalize_question_key(q['question_name']): q for q in new_questions}
    
    all_question_names = set(old_map.keys()) | set(new_map.keys())
    
    detailed_changes = []
    summary = {'unchanged': 0, 'modified': 0, 'added': 0, 'removed': 0}
    
    for question_name in sorted(all_question_names):
        old_q = old_map.get(question_name)
        new_q = new_map.get(question_name)
        
        if old_q and new_q:
            comparison = compare_questions(old_q, new_q)
            
            change_record = {
                'change_type': comparison['change_type'],
                'question_name': old_q['question_name'],
                'old_question_id': old_q['question_id'],
                'new_question_id': new_q['question_id'],
                'similarity': comparison['similarity'],
                'text_diff': comparison['text_diff'],
                'old_text': old_q['question_text'],
                'new_text': new_q['question_text'],
                'old_answers': old_q.get('answers', []),
                'new_answers': new_q.get('answers', [])
            }
            
            detailed_changes.append(change_record)
            summary[comparison['change_type']] += 1
            
        elif new_q and not old_q:
            change_record = {
                'change_type': 'added',
                'question_name': new_q['question_name'],
                'old_question_id': None,
                'new_question_id': new_q['question_id'],
                'new_text': new_q['question_text'],
                'new_answers': new_q.get('answers', [])
            }
            
            detailed_changes.append(change_record)
            summary['added'] += 1
            
        elif old_q and not new_q:
            change_record = {
                'change_type': 'removed',
                'question_name': old_q['question_name'],
                'old_question_id': old_q['question_id'],
                'new_question_id': None,
                'old_text': old_q['question_text'],
                'old_answers': old_q.get('answers', [])
            }
            
            detailed_changes.append(change_record)
            summary['removed'] += 1
    
    summary['total_changes'] = summary['modified'] + summary['added'] + summary['removed']
    
    return {'detailed_changes': detailed_changes, 'summary': summary}


if __name__ == "__main__":
    from .parse_quiz import parse_quiz_xml, extract_version_from_filename
    
    quiz_dir = Path(__file__).resolve().parents[3] / "data" / "comparison" / "quiz"
    xml_files = sorted(quiz_dir.glob("*.xml"))
    
    if len(xml_files) >= 2:
        old_file = xml_files[0]
        new_file = xml_files[1]
        
        print(f"   Old: {old_file.name} ({extract_version_from_filename(old_file.name)})")
        print(f"   New: {new_file.name} ({extract_version_from_filename(new_file.name)})")
        print()
        
        old_questions = parse_quiz_xml(old_file)
        new_questions = parse_quiz_xml(new_file)
        
        comparison = compare_quiz_versions(old_questions, new_questions)
        
        for key, value in comparison['summary'].items():
            print(f"   {key}: {value}")
        print()
        
        print("Detailed Changes:")
        for change in comparison['detailed_changes']:
            if change['change_type'] != 'unchanged':
                print(f"\n   {change['change_type'].upper()}: {change['question_name']}")
                if change['change_type'] == 'modified':
                    print(f"      Diff: {change['text_diff']}")
                    print(f"      Old: {change['old_text'][:80]}...")
                    print(f"      New: {change['new_text'][:80]}...")
                elif change['change_type'] == 'added':
                    print(f"      Text: {change['new_text'][:80]}...")
                elif change['change_type'] == 'removed':
                    print(f"      Text: {change['old_text'][:80]}...")
        
        output_file = quiz_dir / "quiz_changes.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(comparison, f, indent=2, ensure_ascii=False)
        
        print(f"Saved comparison: {output_file}")
