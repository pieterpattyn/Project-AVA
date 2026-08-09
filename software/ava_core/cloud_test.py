from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()

print("Connecting AVA to OpenAI...")

response = client.responses.create(
    model="gpt-5-mini",
    input="Reply with exactly: AVA cloud connection successful."
)

print(response.output_text)
