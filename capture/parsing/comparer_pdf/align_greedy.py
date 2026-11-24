"""Global slide matching algorithm with Greedy"""
from typing import List, Tuple, Optional
import numpy as np
from sentence_transformers import SentenceTransformer


def tokenize(text: str) -> List[str]:
    return [t for t in ''.join(ch.lower() if ch.isalnum() or ch.isspace() else ' ' for ch in text).split() if t]


def jaccard(a: str, b: str) -> float:
    A, B = set(tokenize(a)), set(tokenize(b))
    if not A and not B: return 1.0
    return len(A & B) / max(1, len(A | B))


class SlideEmbedder:
    def __init__(self, model_name: str = "distiluse-base-multilingual-cased-v2"):
        self.model = SentenceTransformer(model_name)

    def embed_list(self, texts: List[str]) -> np.ndarray:
        return self.model.encode(texts)


def cosine_similarity_matrix(embedder: SlideEmbedder, A: List[str], B: List[str]) -> np.ndarray:
    if not A or not B:
        return np.zeros((len(A), len(B)))
    
    embeddings_A = embedder.embed_list(A)
    embeddings_B = embedder.embed_list(B)
    
    # Normalize
    norm_A = np.linalg.norm(embeddings_A, axis=1, keepdims=True) + 0.000000001
    norm_B = np.linalg.norm(embeddings_B, axis=1, keepdims=True) + 0.000000001
    embeddings_A_norm = embeddings_A / norm_A
    embeddings_B_norm = embeddings_B / norm_B
    
    return embeddings_A_norm @ embeddings_B_norm.T


def similarity_matrix(embedder: SlideEmbedder, A: List[str], B: List[str], w_cos=0.7, w_jac=0.3) -> np.ndarray:
    cos = cosine_similarity_matrix(embedder, A, B)
    jac = np.zeros_like(cos)
    for i in range(len(A)):
        for j in range(len(B)):
            jac[i,j] = jaccard(A[i], B[j])
    return w_cos * cos + w_jac * jac


def global_best_match(S: np.ndarray, threshold: float = 0.55) -> List[Tuple[int, int]]:
    """
    1. Find the highest similarity score in the matrix
    2. Match those two slides
    3. Remove that row and column from consideration
    4. Repeat
    """
    m, n = S.shape
    matches = []
    
    # Create a copy
    S_work = S.copy()
    used_old = set()
    used_new = set()
    
    while True:
        # Find best remaining match
        max_val = -1
        best_i, best_j = -1, -1
        
        for i in range(m):
            if i in used_old:
                continue
            for j in range(n):
                if j in used_new:
                    continue
                if S_work[i, j] > max_val:
                    max_val = S_work[i, j]
                    best_i, best_j = i, j
        
        # Stop if no match above threshold
        if max_val < threshold:
            break
        
        # Record the match
        matches.append((best_i, best_j, max_val))
        used_old.add(best_i)
        used_new.add(best_j)
    
    # Sort by similarity
    matches.sort(key=lambda x: x[2], reverse=True)

    return [(i, j) for i, j, _ in matches]


def detect_splits_merges(A: List[str], B: List[str], S: np.ndarray, 
                                matches: List[Tuple[int, int]], 
                                sim_thresh: float = 0.55):

    results = {"pairs": [], "splits": [], "merges": [], "inserts": [], "deletes": []}
    
    matched_old = set(i for i, _ in matches)
    matched_new = set(j for _, j in matches)
    
    splits_found = set()
    splits_new_slides = set()
    
    # perfect matches (similarity = 1.0) excluded
    perfect_matches = {}
    for old_idx, new_idx in matches:
        if S[old_idx, new_idx] >= 0.975:
            perfect_matches[old_idx] = new_idx
    
    for i in range(len(A)):
        if i not in matched_old:
            continue
        
        # Skip if perfect match - its unchanged, not split
        if i in perfect_matches:
            continue
            
        # Find all new slides with high similarity
        high_sim_new = []
        for j in range(len(B)):
            # Skip if this new slide is a perfect match with another old slide already
            is_perfect_match_elsewhere = any(
                old_i in perfect_matches and perfect_matches[old_i] == j 
                for old_i in range(len(A)) if old_i != i
            )
            if is_perfect_match_elsewhere:
                continue
                
            if S[i, j] >= sim_thresh:
                high_sim_new.append(j)
        
        # If matched to 2+ new slides, its split
        if len(high_sim_new) >= 2:
            high_sim_new.sort(key=lambda j: S[i, j], reverse=True)
            results["splits"].append((i, high_sim_new))
            splits_found.add(i)
            splits_new_slides.update(high_sim_new)
    
    # Detect merges
    merges_found = set()
    merges_old_slides = set()
    
    for j in range(len(B)):
        if j not in matched_new:
            continue
        
        # Skip if perfect match - its unchanged, not a merge
        is_perfect_match = any(
            old_i in perfect_matches and perfect_matches[old_i] == j
            for old_i in range(len(A))
        )
        if is_perfect_match:
            continue
            
        # Find all old slides with high similarity to this new slide
        # Exclude old slides already identified as splits and those that are perfect matches with new slides already
        high_sim_old = []
        for i in range(len(A)):
            if i in splits_found:
                continue
            
            if i in perfect_matches and perfect_matches[i] != j:
                continue
                
            if S[i, j] >= sim_thresh:
                high_sim_old.append(i)
        
        # If 2+ old slides match new slide, its a merge
        if len(high_sim_old) >= 2:
            high_sim_old.sort(key=lambda i: S[i, j], reverse=True)
            results["merges"].append((high_sim_old, j))
            merges_found.add(j)
            merges_old_slides.update(high_sim_old)
    
    # build pairs list: only include matches NOT in splits/merges
    for old_idx, new_idx in matches:
        if old_idx not in splits_found and new_idx not in merges_found:
            results["pairs"].append((old_idx, new_idx))
    
    # Find unmatched slides
    for i in range(len(A)):
        if i not in matched_old and i not in splits_found and i not in merges_old_slides:
            results["deletes"].append(i)
    
    for j in range(len(B)):
        if j not in matched_new and j not in merges_found and j not in splits_new_slides:
            results["inserts"].append(j)
    
    return results


def compare_slide_versions_global(old_slides: List[str], new_slides: List[str], 
                                  embedder: Optional[SlideEmbedder] = None,
                                  w_cos: float = 0.7, w_jac: float = 0.3,
                                  sim_thresh: float = 0.55) -> dict:
    if embedder is None:
        embedder = SlideEmbedder()
    
    S = similarity_matrix(embedder, old_slides, new_slides, w_cos, w_jac)
    
    matches = global_best_match(S, threshold=sim_thresh)
    
    results = detect_splits_merges(old_slides, new_slides, S, matches, sim_thresh)
    
    results["similarity_matrix"] = S
    results["match_method"] = "global_best_match"
    
    return results

# -------------------------- test of just this module
if __name__ == "__main__":

    old_slides = [
        "Lernziele: Datenstrukturen, Listen, Queue, Stack, Heap, Bäume, Algorithmen, Suchen, Sortieren, Aufwandsabschätzung, Korrektheit",
        "Slide B: Other content",
        "Slide C: More content"
    ]
    
    new_slides = [
        "Lernziele: Datenstrukturen, Listen, Queue (Warteschlange), Stack (Stapel), Heap (Haufen), Bäume",  # First half
        "Lernziele: Elementare Algorithmen, Suchen, Sortieren, Aufwandsabschätzung (Komplexität), Korrektheit",  # Second half
        "Slide B: Other content",
        "Slide C: More content"
    ]
    
    results = compare_slide_versions_global(old_slides, new_slides, sim_thresh=0.55)
    print("Similarity matrix:")
    print(np.round(results["similarity_matrix"], 2))
    
    print("Matched pairs:")
    for old_idx, new_idx in results["pairs"]:
        sim_score = results["similarity_matrix"][old_idx, new_idx]
        print(f"  Old[{old_idx}] - New[{new_idx}] (sim={sim_score:.3f})")
    
    print("Splits detected:")
    for old_idx, new_indices in results["splits"]:
        print(f"  Old[{old_idx}] - New{new_indices}")
        for new_idx in new_indices:
            sim_score = results["similarity_matrix"][old_idx, new_idx]
            print(f" New[{new_idx}]: '{new_slides[new_idx][:60]}...' (sim={sim_score:.3f})")
    
    print("New slides added:")
    for new_idx in results["inserts"]:
        print(f"New[{new_idx}]: '{new_slides[new_idx]}'")
    
    print("Slides removed:")
    for old_idx in results["deletes"]:
        print(f"Old[{old_idx}]: '{old_slides[old_idx]}'")
    
    
    old_0_in_pairs = any(old_idx == 0 for old_idx, _ in results["pairs"])
    old_0_in_splits = any(old_idx == 0 for old_idx, _ in results["splits"])
    
    if old_0_in_pairs and old_0_in_splits:
        print("BUG: Old 0 appears in BOTH pairs and splits")
    elif old_0_in_splits:
        print("CORRECT: Old 0 correctly appears only in splits")
    

    new_0_in_pairs = any(new_idx == 0 for _, new_idx in results["pairs"])
    new_1_in_pairs = any(new_idx == 1 for _, new_idx in results["pairs"])
    new_0_in_inserts = 0 in results["inserts"]
    new_1_in_inserts = 1 in results["inserts"]
    
    if (new_0_in_pairs or new_1_in_pairs) and (new_0_in_inserts or new_1_in_inserts):
        print("BUG: Split slides appear in pairs/inserts")
    elif not new_0_in_pairs and not new_1_in_pairs and not new_0_in_inserts and not new_1_in_inserts:
        print("CORRECT: New[0] and New[1] correctly appear only in splits")
