import obspython as obs
import queue
import threading
import urllib.request
import base64
import json
import websocket
import os
from pathlib import Path
from dotenv import load_dotenv

OVERLAY_SCENE = "VTubing"
GROUP_SOURCE = "Gacha Overlay"
TEXT_SOURCE = "Gacha Text"
IMAGE_SOURCE = "Gacha Image"

load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(override=True)
WS_URL = os.getenv("OBS_WS_URL", "ws://127.0.0.1:8765")
WS_SECRET = os.getenv("WS_SECRET")

# Thread communication
events = queue.Queue()
stop_event = threading.Event()
obs_socket = None

def script_description():
    return "This is a gacha thingy!"

def set_popup_visible(visible):
    scene_source = obs.obs_get_source_by_name(OVERLAY_SCENE)
    scene = obs.obs_scene_from_source(scene_source)
    item = obs.obs_scene_find_source(scene, GROUP_SOURCE)

    obs.obs_sceneitem_set_visible(item, visible)

def change_text(text):
    update_source(TEXT_SOURCE, "text", text)

def update_source(source_name, key, value):
    source = obs.obs_get_source_by_name(source_name)

    if source is None:
        obs.script_log(
            obs.LOG_WARNING, f"Source not found: {source_name}"
        )
        return

    try:
        settings = obs.obs_source_get_settings(source)

        try:
            obs.obs_data_set_string(settings, key, value)
            obs.obs_source_update(source, settings)
        finally:
            obs.obs_data_release(settings)
    finally:
        obs.obs_source_release(source)

def hide_gacha():
    obs.timer_remove(hide_gacha)
    set_popup_visible(False)

def show_gacha(username, data):
    change_text(f"{username} rolled the Gacha!")
    set_popup_visible(True)
    obs.timer_remove(hide_gacha)
    obs.timer_add(hide_gacha, 10000)

# def script_properties():
#     props = obs.obs_properties_create()

#     obs.obs_properties_add_button(
#         props, "test", "Test Gacha", test_gacha
#     )

#     return props

def download_gacha_image(data):
    file_path = Path(__file__).resolve().parent / "Gacha Drops"
    file_path.mkdir(parents=True, exist_ok=True)
    
    filename = data['image_filename']
    
    if Path(filename).suffix.lower() not in {
        ".png", ".jpg", ".jpeg", ".webp", ".gif"
    }:
        raise ValueError("Unsupported image format")

    image_path = file_path / filename

    if image_path.is_file():
        return str(image_path)
    
    image_bytes = base64.b64decode(
        data["image_data"],
        validate=True
    )
    
    temp_path = image_path.with_name(filename + ".part")
    temp_path.write_bytes(image_bytes)
    temp_path.replace(image_path)

    return str(image_path)

def script_load(settings):
    set_popup_visible(False)
    obs.script_log(obs.LOG_INFO, "Script loaded mmmm")
    obs.script_log(obs.LOG_INFO, WS_URL)
    
    obs.timer_add(process_events, 100)

    if not WS_SECRET:
        obs.script_log(
            obs.LOG_ERROR,
            "WS_SECRET is missing!"
        )
        return

    stop_event.clear()

    threading.Thread(
        target=listen_to_server,
        daemon=True
    ).start()

def script_unload():
    stop_event.set()

    if obs_socket is not None:
        obs_socket.close()

    obs.timer_remove(process_events)
    obs.timer_remove(hide_gacha)

current_gacha = None

def process_events():
    global current_gacha

    while not events.empty():
        event_type, data = events.get()

        if event_type == "gacha":
            current_gacha = data
            show_gacha(data["username"], data["item"])

        elif event_type == "log":
            obs.script_log(obs.LOG_INFO, data)

        elif event_type == "error":
            obs.script_log(obs.LOG_ERROR, data)

def listen_to_server():
    global obs_socket

    def on_open(ws):
        # Authenticate with your remote server
        ws.send(json.dumps({
            "token": WS_SECRET
        }))
        
        #Download all images on startup
        ws.send(json.dumps({
            "type": "sync_images"
        }))

        events.put(("log", "Connected to WebSocket!"))

    def on_message(ws, message):
        try:
            data = json.loads(message)
            
            if data.get("type") == "gacha_image":
                download_gacha_image(data)

            elif data.get("type") == "sync_complete":
                print("yippeee download worked")

            elif data.get("type") == "gacha":
                events.put(("gacha", data))

        except Exception as error:
            events.put(("error", str(error)))

    def on_error(ws, error):
        events.put(("error", str(error)))

    def on_close(ws, close_code, close_message):
        events.put(("log", "WebSocket disconnected!"))

    while not stop_event.is_set():
        try:
            obs_socket = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )

            obs_socket.run_forever()

        except Exception as error:
            events.put(("error", str(error)))

        # Wait 5 seconds before reconnecting
        if stop_event.wait(10):
            break
