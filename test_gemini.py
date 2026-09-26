"""Check the Gemini API key works and list the free Flash models available."""
import os

from dotenv import load_dotenv
from google import genai

load_dotenv()  # reads .env into environment variables
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise SystemExit("GEMINI_API_KEY not found. Check your .env file.")

client = genai.Client(api_key=api_key)

# 1. List the Flash models your key can use for text generation
print("Flash models available to you:")
flash_models = []
for model in client.models.list():
    name = model.name.replace("models/", "")
    if "flash" in name and "generateContent" in (model.supported_actions or []):
        flash_models.append(name)
        print("  ", name)

# 2. Send a tiny test message to the first one
test_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
response = client.models.generate_content(
    model=test_model,
    contents="Say 'Hello, data agent!' and nothing else.",
)
print(f"\nTest with {test_model}:")
print(response.text)