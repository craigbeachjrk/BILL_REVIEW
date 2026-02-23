import os, google.generativeai as genai
genai.configure(api_key=os.getenv("GEMINI_API_KEY", "AIzaSyAFvRWaM5ADsL51dR2XLNoZZIFo-vKC_to"))

prompt = "Reply ONLY with the word PONG."
resp   = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt)
print("Gemini said:", resp.text.strip())