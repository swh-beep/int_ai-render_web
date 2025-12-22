import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("NANOBANANA_API_KEY_1")

if not api_key:
    print("No API Key found.")
else:
    genai.configure(api_key=api_key)
    print("Listing available models...")
    try:
        for m in genai.list_models():
            print(f"Name: {m.name}")
            print(f"Supported generation methods: {m.supported_generation_methods}")
            print("-" * 20)
    except Exception as e:
        print(f"Error listing models: {e}")
