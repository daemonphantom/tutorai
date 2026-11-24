from sentence_transformers import SentenceTransformer
import numpy as np
from .start import Neo4jConnection

class TextEmbedder:
    def __init__(self, 
                 neo_connection: Neo4jConnection,
                 model_name: str = "distiluse-base-multilingual-cased-v2", # EMBEDDING MODEL
                 dims: int = 512): 
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.dims = dims
        self.db = neo_connection.db

    # Convert single text to embedding
    def embed_text(self, text: str) -> np.ndarray:
        return self.model.encode([text])[0]
    
    # Convert multiple texts to embeddings
    def embed_list(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts)