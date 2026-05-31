import os
import time
import uuid

import pyttsx3
from google import genai
from google.genai import types
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play as elevenlabs_play

load_dotenv()


def ensure_ffmpeg_on_path():
    candidate_dirs = [
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft",
            "WinGet",
            "Packages",
        ),
        os.path.join(os.environ.get("ProgramFiles", ""), "ffmpeg", "bin"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "ffmpeg", "bin"),
    ]

    for base_dir in candidate_dirs:
        if not base_dir or not os.path.isdir(base_dir):
            continue

        for root, _, files in os.walk(base_dir):
            if "ffplay.exe" in files:
                current_path = os.environ.get("PATH", "")
                if root not in current_path:
                    os.environ["PATH"] = root + os.pathsep + current_path
                return root

    return None


FFPLAY_DIR = ensure_ffmpeg_on_path()

MODEL_ID = "gemini-2.5-flash"
TYPE_MODE = "type"
TEST_MODE = "test"
TEST_GEMINI_ONLY = "gemini_only"
TEST_GEMINI_AND_ELEVENLABS = "gemini_and_elevenlabs"
SYSTEM_PROMPT = (
    "You are a witty film, tv and animation lecturer currently "
    "speaking at NAHEMI with University of Westminster's Stephen Ryley. "
    "Always answer in 5 short full sentences, with enough detail to sound "
    "a real spoken answer. "
    "Do not repeat the user's prompt or restate the same idea twice. "
    "First sentence be funny, last sentence be profound, and make the middle "
    "sentences as useful as possible. "
)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
ELEVENLABS_OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__),
    "elevenlabs_output",
)

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

elevenlabs_client = None
if ELEVENLABS_API_KEY:
    try:
        elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    except Exception as e:
        print(
            "ElevenLabs client initialization failed, using backup TTS: "
            f"{e}"
        )

search_tool = types.Tool(
    google_search=types.GoogleSearch()
)

chat = client.chats.create(
    model=MODEL_ID,
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[search_tool],
    )
)

os.makedirs(ELEVENLABS_OUTPUT_DIR, exist_ok=True)


def wait_for_enter(message):
    input(message)


def prompt_for_test_variant():
    print("Choose a test mode:")
    print("1) Gemini only")
    print("2) Gemini + ElevenLabs")

    while True:
        choice = input("> ").strip().lower()
        if choice in {"1", "gemini", "gemini only"}:
            return TEST_GEMINI_ONLY
        if choice in {
            "2",
            "both",
            "gemini + elevenlabs",
            "gemini and elevenlabs",
        }:
            return TEST_GEMINI_AND_ELEVENLABS
        print("Please choose 1 or 2.")


def prompt_for_mode():
    print("Choose a mode:")
    print("1) Type mode")
    print("2) Test mode")

    while True:
        choice = input("> ").strip().lower()
        if choice in {"1", "type", "type mode"}:
            return TYPE_MODE, None
        if choice in {"2", "test", "test mode"}:
            return TEST_MODE, prompt_for_test_variant()
        print("Please choose 1 or 2.")


def run_prompt_flow(
    prompt,
    save_elevenlabs_audio=True,
    wait_for_audio_enter=True,
):
    success, response_text, audio_bytes, audio_path = process_prompt(
        prompt,
        save_elevenlabs_audio=save_elevenlabs_audio,
        defer_speaking=True,
    )

    if not success:
        return False

    if wait_for_audio_enter:
        wait_for_enter("Press Enter to play the response...")

    if audio_path:
        print(f"[Saved response ready at {audio_path}]")

    if audio_bytes:
        elevenlabs_play(audio_bytes)
    else:
        speak_with_pyttsx3(response_text)

    return True


def speak_with_elevenlabs(text, save_audio=False, play_audio=True):
    if not elevenlabs_client or not ELEVENLABS_VOICE_ID:
        return False, None

    try:
        audio_stream = elevenlabs_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID,
            text=text,
            model_id=ELEVENLABS_MODEL_ID,
        )
        audio_bytes = (
            audio_stream if isinstance(audio_stream, bytes)
            else b"".join(audio_stream)
        )
        output_path = None

        if save_audio:
            filename = (
                f"elevenlabs_{time.strftime('%Y%m%d_%H%M%S')}_"
                f"{uuid.uuid4().hex}.mp3"
            )
            output_path = os.path.join(ELEVENLABS_OUTPUT_DIR, filename)
            with open(output_path, "wb") as output_file:
                output_file.write(audio_bytes)
            print(f"[ElevenLabs saved to {output_path}]")

        if play_audio:
            elevenlabs_play(audio_bytes)
        return True, output_path
    except Exception as e:
        print(f"ElevenLabs TTS failed, falling back to pyttsx3: {e}")
        return False, None


def speak_with_pyttsx3(text):
    engine = pyttsx3.init()
    engine.setProperty('rate', 150)
    engine.say(text)
    engine.runAndWait()
    engine.stop()


def speak(text, save_elevenlabs_audio=False):
    if not text:
        return

    print(f"Output: {text}")

    success, _ = speak_with_elevenlabs(text, save_audio=save_elevenlabs_audio)
    if success:
        time.sleep(0.3)
        return

    speak_with_pyttsx3(text)
    time.sleep(0.3)


def process_prompt(prompt, save_elevenlabs_audio=False, defer_speaking=False):
    max_retries = 3

    for attempt in range(max_retries):
        try:
            print("Thinking...")
            response = chat.send_message(prompt)

            if (
                response.candidates
                and response.candidates[0].grounding_metadata
                and (
                    response.candidates[0]
                    .grounding_metadata.web_search_queries
                )
            ):
                print("[System: Grounded using Google Search] ")

            response_text = (response.text or "").strip()
            if not response_text:
                print("AI Error: Gemini returned no text to speak.")
                return False, "", None, None

            if defer_speaking:
                audio_bytes = None
                audio_path = None
                if elevenlabs_client and ELEVENLABS_VOICE_ID:
                    audio_stream = elevenlabs_client.text_to_speech.convert(
                        voice_id=ELEVENLABS_VOICE_ID,
                        text=response_text,
                        model_id=ELEVENLABS_MODEL_ID,
                    )
                    audio_bytes = (
                        audio_stream if isinstance(audio_stream, bytes)
                        else b"".join(audio_stream)
                    )

                    if save_elevenlabs_audio:
                        filename = (
                            f"elevenlabs_{time.strftime('%Y%m%d_%H%M%S')}_"
                            f"{uuid.uuid4().hex}.mp3"
                        )
                        audio_path = os.path.join(
                            ELEVENLABS_OUTPUT_DIR,
                            filename,
                        )
                        with open(audio_path, "wb") as output_file:
                            output_file.write(audio_bytes)
                        print(f"[ElevenLabs saved to {audio_path}]")

                print(f"AI: {response_text}")
                return True, response_text, audio_bytes, audio_path

            speak(response_text, save_elevenlabs_audio=save_elevenlabs_audio)
            return True, response_text, None, None
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg and attempt < max_retries - 1:
                print(
                    "Rate limit hit (429). You only have 5 requests per "
                    "minute. Waiting 15 seconds..."
                )
                time.sleep(15)
            elif "503" in error_msg and attempt < max_retries - 1:
                wait = (2 ** attempt)
                print(f"Server busy (503), retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"AI Error: {e}")
                return False, "", None, None


def main():
    print(f"--- AI Online ({MODEL_ID}) ---")

    mode, test_variant = prompt_for_mode()
    use_elevenlabs = (
        mode == TYPE_MODE
        or test_variant == TEST_GEMINI_AND_ELEVENLABS
    )

    if mode == TYPE_MODE:
        print("--- Type Mode Enabled ---")
    else:
        print("--- Test Mode Enabled ---")
        if test_variant == TEST_GEMINI_ONLY:
            print("--- Gemini Only ---")
        else:
            print("--- Gemini + ElevenLabs ---")

    while True:
        print("\nType your question and press Enter...")
        prompt = input("> ").strip()

        if not prompt:
            continue

        print(f"Heard: '{prompt}'")

        if not run_prompt_flow(
            prompt,
            save_elevenlabs_audio=use_elevenlabs,
            wait_for_audio_enter=use_elevenlabs,
        ):
            continue


if __name__ == "__main__":
    main()
