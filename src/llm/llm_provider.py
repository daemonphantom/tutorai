import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

@dataclass
class LLMConfig:
    """Configuration for local Ollama LLM models"""
    model: str = os.getenv('OLLAMA_MODEL', 'gpt-oss:20b') # 'llama3.1:8b-instruct-q4_K_M' # 'gpt-oss:20b'
    base_url: Optional[str] = os.getenv('OLLAMA_BASE_URL')  # For local Ollama server
    num_predict: int = 2048 # 2048 - 4096 - 8192 (max response length in tokens) -> short chat responses are better. 
    num_ctx: int = 32768
    temperature: float = 0.8

class OllamaProvider:
    """Local Ollama integration"""
    
    def __init__(self):
        self.config = LLMConfig()
        try:
            import requests
            self.session = requests.Session()
            self.requests = requests  
            self.base_url = self.config.base_url or "http://localhost:11434"
        except ImportError:
            raise ImportError("Install requests: pip install requests") from None
    
    def generate(self, prompt: str) -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.config.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": self.config.num_predict,
                        "num_ctx": self.config.num_ctx,
                        "temperature": self.config.temperature
                    }
                }
            )
            
            if response.status_code != 200:
                return f"Error: HTTP {response.status_code} - {response.text}"
            
            response_data = response.json()
            if "response" not in response_data:
                return f"Error: Missing 'response' key in Ollama response. Got keys: {list(response_data.keys())}"
            
            return response_data["response"]
            
        except self.requests.exceptions.ConnectionError:
            return "Error: Cannot connect to Ollama. Make sure it's running with 'ollama serve'"
        except Exception as e:
            return f"Error generating response: {str(e)}"

    def chat(self, messages: list, options: dict = None) -> str:
        """Chat interface"""
        try:
            chat_options = options if options is not None else {
                "num_predict": self.config.num_predict,
                "num_ctx": self.config.num_ctx,
                "temperature": self.config.temperature
            }
            
            response = self.session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.config.model,
                    "messages": messages,
                    "stream": False,
                    "options": chat_options
                }
            )
            
            if response.status_code != 200:
                return f"Error: HTTP {response.status_code} - {response.text}"
            
            response_data = response.json()
            if "message" not in response_data:
                return f"Error: Missing 'message' key in Ollama chat response. Got keys: {list(response_data.keys())}"
            
            return response_data["message"]["content"]
            
        except self.requests.exceptions.ConnectionError:
            return "Error: Cannot connect to Ollama. Make sure it's running with 'ollama serve'"
        except Exception as e:
            return f"Error generating chat response: {str(e)}"

