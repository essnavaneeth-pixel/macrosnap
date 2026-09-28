import json
from google import genai
from google.genai import types
import streamlit as st
from prompts import SYSTEM_PROMPT,WELCOME_MESSAGE_TEMPLATE,SUMMARY_REQUEST_PROMPT
from twilio.rest import Client as TwilioClient

GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
TWILIO_ACCOUNT_SID = st.secrets["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = st.secrets["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_FROM = st.secrets["TWILIO_WHATSAPP_FROM"]
TWILIO_CONTENT_SID = st.secrets["TWILIO_CONTENT_SID"]

@st.cache_resource
def get_gemini_client():
    # 503 responses mean that Gemini is temporarily out of capacity. Retry only
    # transient HTTP failures, with a short exponential backoff.
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=5,
                initial_delay=1.0,
                max_delay=12.0,
                exp_base=2.0,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            )
        ),
    )

@st.cache_resource
def get_twilio_client():
    return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


twilio_client = get_twilio_client()
gemini_client = get_gemini_client()
MODEL_NAME = "gemini-3.8-flash"
GENERATION_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    # Nutrition estimates do not need the model's default medium reasoning level.
    # A shorter response also lowers load and makes capacity errors less likely.
    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
    max_output_tokens=512,
)

def clean_whatsapp_text(text):
    if not text:
        return "No Nutrition summary available"
    text=" ".join(text.split()) #collapse whitespace
    return text[:1500] + "..." if len(text) > 1500 else text

def send_whatsapp(to_number, user_name, summary):
    try:
        content_variable = json.dumps(
            {"1": user_name, "2": clean_whatsapp_text(summary)},ensure_ascii=False
        )
        message = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{to_number}",
            content_sid=TWILIO_CONTENT_SID,
            content_variables=content_variable,
        )
        return True, message.sid
    except Exception as error:
        return False, str(error)


def render_message(message):
    with st.chat_message(message["role"]):
        if message["kind"] == "text":
            st.write(message["content"])
        elif message["kind"] == "image":
            st.image(message["content"])    

def add_message(role, kind, content):
    st.session_state.messages.append({"role": role, "kind": kind, "content": content})

def ask_gemini(parts):
    try:
        response = st.session_state.chat.send_message(parts)
        if not response.text:
            return None, "Gemini returned an empty response. Please try again."
        return response.text, None
    except Exception as error:
        error_text = str(error)
        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            return None, (
                "You've reached this Gemini model's current quota. Wait for your "
                "quota to reset or enable billing in Google AI Studio, then try again."
            )
        if "503" in error_text or "UNAVAILABLE" in error_text:
            return None, (
                "Gemini is temporarily busy. Please try again in a minute; "
                "your meal log has not been lost."
            )
        return None, "I couldn't analyze that right now. Please try again."

if "onboarded" not in st.session_state:
    st.title("MacroSnap 🥗")
    st.caption("Snap it. Track it. Text yourself the results.")
    with st.form("onboarding_form"):
        name = st.text_input("Your name")
        whatsapp_number = st.text_input(
               "WhatsApp Number (with country code)",
               placeholder="+91XXXXXXXXXX",
               help="This is the number MacroSnap will text you summary to.",
               )                       
        submitted = st.form_submit_button("Lets go 🔥")
    if submitted:
        if not name.strip() or not whatsapp_number.strip():
            st.warning("Please fill both your name and WhatsApp number to continue.")
        else:
            st.session_state.name = name.strip()
            st.session_state.whatsapp_number = whatsapp_number.strip()
            # activation of my ai
            st.session_state.chat = gemini_client.chats.create(
                model=MODEL_NAME,
                config=GENERATION_CONFIG,
            )
            st.session_state.messages = [{
                "role": "assistant",
                "kind": "text",
                "content": WELCOME_MESSAGE_TEMPLATE.format(name=st.session_state.name),
            }]
            st.session_state.onboarded = True
            st.rerun()
    st.stop()
#create a chat interface     
header_col,button_col = st.columns([5,2],vertical_alignment="center")
with header_col:
    st.title("MacroSnap 🥗")
with button_col:
    send_disabled = len(st.session_state.messages) <= 1
    if st.button("send summary to whatsapp",disabled=send_disabled,use_container_width=True):
        with st.spinner("Summarizing your day..."): 
             summary, error = ask_gemini([SUMMARY_REQUEST_PROMPT])
        if error:
            st.error(error)
        else:
            success,info=send_whatsapp(st.session_state.whatsapp_number,st.session_state.name,summary)
            if success:
                st.success("SENT! Check your WhatsApp for the summary.")
            else:
                st.error(f"Couldn't send that message: {info}")

st.caption(f"Logged in as: {st.session_state.name} - updates go to {st.session_state.whatsapp_number}")
if "messages" not in st.session_state:
    st.session_state.messages = []
if not st.session_state.messages:
    st.session_state.messages.append({
        "role": "assistant",
        "kind": "text",
        "content": WELCOME_MESSAGE_TEMPLATE.format(name=st.session_state.name),
    })
for message in st.session_state.messages:
    render_message(message)

user_input = st.chat_input(
    "Ask a question, or attach a photo of your meal to get started! 🍽️",
    accept_file=True,
    file_type=["jpg", "jpeg", "png"],
)
if user_input:
    photo = user_input.files[0] if user_input.files else None
    text = user_input.text
    parts = [] 
    if photo is not None:
       photo_bytes = photo.getvalue()
       add_message("user","image",photo_bytes)
       parts.append(types.Part.from_bytes(data=photo_bytes, mime_type=photo.type))
    if text:
        add_message("user","text",text)
        parts.append(text)
    elif photo is not None:
        parts.append("What is this meal? Give me calories, macros, and a short summary of the meal.")
    with st.spinner("Crushing the numbers..."):    
        answer, error = ask_gemini(parts)
    if error:
        answer = error
    add_message("assistant","text",answer)    
