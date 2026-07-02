# --- START OF FILE audio_handler.py ---

import os
import json
import re
import queue
import threading
import urllib.request
import collections
import numpy as np

# Base directory where this python code actually lives
REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# Audio/AI Dependencies (Handled gracefully)
try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    from kokoro_onnx import Kokoro
except ImportError:
    Kokoro = None

try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Input, Button
from textual.screen import ModalScreen
from textual import on

AUDIO_CONFIG_FILE = "audio_config.json"

def load_audio_config():
    default_config = {
        "tts_voice": "af_sarah",
        "tts_speed": 1.1,
        "stt_start_words": "AGENT, ASSISTANT, COMPUTER",
        "stt_stop_words": "STOP LISTENING, END DICTATION",
        "stt_send_words": "EXECUTE, FINISH, SEND IT",
        "stt_delete_words": "DELETE LAST, REMOVE LAST, SCRAP THAT",
        "stt_device": "",
        "stt_energy_threshold": 0.005,
        "stt_silence_timeout": 1.2,
        "stt_pre_roll": 0.5,
        "stt_min_speech_duration": 0.4
    }
    if os.path.exists(AUDIO_CONFIG_FILE):
        try:
            with open(AUDIO_CONFIG_FILE, "r") as f:
                data = json.load(f)
                default_config.update(data)
        except Exception:
            pass
    return default_config

def save_audio_config(config):
    try:
        with open(AUDIO_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception:
        pass

def clean_markdown_for_speech(text: str) -> str:
    """Strips markdown artifacts so TTS reads naturally, while PRESERVING linebreaks and punctuation."""
    if not text: return ""
    # 1. Remove large code blocks
    text = re.sub(r'```.*?```', '\n[Code block omitted]\n', text, flags=re.DOTALL)
    # 2. Extract text from inline code ticks (e.g. `variable` -> variable)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 3. Strip URLs
    text = re.sub(r'http[s]?://\S+', 'link omitted', text)
    # 4. Extract text from markdown links
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # 5. Strip formatting characters but KEEP newlines and punctuation
    text = re.sub(r'[*_#~>]+', '', text)
    # 6. Clean up horizontal whitespace (spaces/tabs) without touching newlines
    text = re.sub(r'[ \t]+', ' ', text)
    # 7. Collapse excessive newlines into standard paragraphs
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# --- TEXTUAL UI MODAL ---

class AudioConfigModal(ModalScreen[str]):
    DEFAULT_CSS = """
    AudioConfigModal { align: center middle; background: $background 60%; }
    #audio_config_dialog { width: 70; height: auto; border: round $primary; background: $surface; padding: 1 2; }
    .config_row { layout: horizontal; height: auto; margin-top: 1; }
    .config_row Input { width: 1fr; margin-right: 1; }
    #audio_actions { margin-top: 1; align: right middle; }
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="audio_config_dialog"):
            yield Label("🎤 Audio Configuration", classes="pane_title")
            
            yield Label("\nTTS Settings (System Default):")
            with Horizontal(classes="config_row"):
                yield Input(id="tts_voice", placeholder="Voice (e.g., af_sarah)")
                yield Input(id="tts_speed", placeholder="Speed (e.g., 1.1)")
                
            yield Label("\nSTT Settings (Hotword Mode):")
            with Horizontal(classes="config_row"):
                yield Input(id="stt_start", placeholder="Start Words (comma separated)")
                yield Input(id="stt_stop", placeholder="Stop/Pause Words (comma separated)")
            with Horizontal(classes="config_row"):
                yield Input(id="stt_send", placeholder="Send/Execute Words (comma separated)")
                yield Input(id="stt_delete", placeholder="Delete Words (comma separated)")
            with Horizontal(classes="config_row"):
                yield Input(id="stt_device", placeholder="Mic Device ID (leave blank for default)")
                
            with Horizontal(id="audio_actions"):
                yield Button("Save", id="save_audio_btn", variant="success")
                yield Button("Cancel", id="cancel_audio_btn", variant="error")

    def on_mount(self):
        config = load_audio_config()
        self.query_one("#tts_voice", Input).value = config.get("tts_voice", "")
        self.query_one("#tts_speed", Input).value = str(config.get("tts_speed", ""))
        self.query_one("#stt_start", Input).value = config.get("stt_start_words", "")
        self.query_one("#stt_stop", Input).value = config.get("stt_stop_words", "")
        self.query_one("#stt_send", Input).value = config.get("stt_send_words", "")
        self.query_one("#stt_delete", Input).value = config.get("stt_delete_words", "")
        self.query_one("#stt_device", Input).value = str(config.get("stt_device", ""))

    @on(Button.Pressed, "#save_audio_btn")
    def save_btn(self):
        try: speed = float(self.query_one("#tts_speed", Input).value)
        except: speed = 1.1
        
        device_val = self.query_one("#stt_device", Input).value
        try: device = int(device_val) if device_val.strip() else ""
        except: device = ""
        
        config = load_audio_config()
        config.update({
            "tts_voice": self.query_one("#tts_voice", Input).value,
            "tts_speed": speed,
            "stt_start_words": self.query_one("#stt_start", Input).value,
            "stt_stop_words": self.query_one("#stt_stop", Input).value,
            "stt_send_words": self.query_one("#stt_send", Input).value,
            "stt_delete_words": self.query_one("#stt_delete", Input).value,
            "stt_device": device
        })
        save_audio_config(config)
        self.dismiss("update")

    @on(Button.Pressed, "#cancel_audio_btn")
    def cancel_btn(self):
        self.dismiss("cancel")

# --- TTS MANAGER ---

AUDIO_LOCK = threading.Lock()

class TTSManager:
    def __init__(self):
        self.model = None
        self.audio_queue = queue.Queue()
        self.generator_queue = queue.Queue() # Queues sentences dynamically alongside their assigned voice
        self.stop_event = threading.Event() 
        self.split_pattern = re.compile(r'([.!?]+(?:\s|\n)|(?:\n+))')
        
        self.worker_thread = threading.Thread(target=self._audio_worker, daemon=True)
        self.worker_thread.start()
        
        self.generator_thread = threading.Thread(target=self._generator_worker, daemon=True)
        self.generator_thread.start()
        
        self.text_stream_buffer = ""
        self.reload_config()

    def reload_config(self):
        config = load_audio_config()
        self.voice = config.get("tts_voice", "af_sarah")
        self.speed = config.get("tts_speed", 1.1)

    def load_model(self):
        if not Kokoro or not sd: return
        if not self.model:
            model_path = os.path.join(REPO_DIR, "kokoro-v1.0.onnx")
            voices_path = os.path.join(REPO_DIR, "voices-v1.0.bin")
            self.model = Kokoro(model_path, voices_path)

    def stop_all_audio(self):
        """Thread-safe request to stop audio."""
        self.stop_event.set()
        
        # Drain the generator queue
        with self.generator_queue.mutex:
            self.generator_queue.queue.clear()
            
        # Drain the audio queue
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except: break
            
        self.text_stream_buffer = ""

    def _audio_worker(self):
        while True:
            item = self.audio_queue.get()
            if item is None: break
            
            if self.stop_event.is_set():
                self.audio_queue.task_done()
                continue
                
            samples, sample_rate = item
            
            try:
                with AUDIO_LOCK:
                    if sd and not self.stop_event.is_set():
                        sd.play(samples, sample_rate)
                        while not self.stop_event.is_set():
                            try:
                                if not sd.get_stream().active: break
                            except: break
                            sd.sleep(50)
                        
                        if self.stop_event.is_set():
                            sd.stop()
            except Exception:
                pass
            finally:
                self.audio_queue.task_done()

    def _generator_worker(self):
        """Dedicated thread to generate audio sequentially using the voice assigned to each chunk."""
        while True:
            item = self.generator_queue.get()
            if item is None: break
            
            if self.stop_event.is_set():
                self.generator_queue.task_done()
                continue
                
            text, voice_to_use = item
            try:
                self.load_model()
                if self.model and not self.stop_event.is_set():
                    samples, sample_rate = self.model.create(text, voice=voice_to_use, speed=self.speed, lang="en-us")
                    if not self.stop_event.is_set():
                        self.audio_queue.put((samples, sample_rate))
            except Exception:
                pass
            finally:
                self.generator_queue.task_done()

    def start_stream(self, voice=None):
        """Prepares the TTS to receive a chunked token stream."""
        self.stop_event.clear()
        self.text_stream_buffer = ""
        if voice:
            self.voice = voice
        else:
            self.reload_config()

    def stream_text(self, chunk: str):
        """Appends chunks and dispatches completed sentences to the generator instantly, coupled with the active voice."""
        if not chunk or self.stop_event.is_set(): return
        
        self.text_stream_buffer += chunk
        parts = self.split_pattern.split(self.text_stream_buffer)
        
        self.text_stream_buffer = ""
        buffer = ""
        
        for p in parts:
            buffer += p
            if self.split_pattern.match(p): 
                to_speak = clean_markdown_for_speech(buffer.strip())
                if to_speak:
                    self.generator_queue.put((to_speak, self.voice))
                buffer = ""
                
        # Keep leftover (incomplete sentence) in the buffer
        if buffer.strip():
            self.text_stream_buffer = buffer

    def flush_stream(self):
        """Pushes any remaining text in the buffer to the generator."""
        if self.text_stream_buffer.strip() and not self.stop_event.is_set():
            to_speak = clean_markdown_for_speech(self.text_stream_buffer.strip())
            if to_speak:
                self.generator_queue.put((to_speak, self.voice))
        self.text_stream_buffer = ""

    def speak(self, text: str, voice=None):
        """Instant speaking of whole blocks."""
        self.start_stream(voice)
        self.stream_text(text)
        self.flush_stream()

# --- STT MANAGER ---

class STTManager:
    def __init__(self, callback, log_callback=None, tts_manager=None):
        self.callback = callback
        self.log_callback = log_callback
        self.tts_manager = tts_manager
        self.is_running = False
        self.mode = None
        self.thread = None
        self.audio_queue = queue.Queue()
        self.trigger_recognizer = None
        self.whisper_recognizer = None
        self.SAMPLE_RATE = 16000
        self.CHUNK_DURATION = 0.1
        self.reload_config()

    def reload_config(self):
        config = load_audio_config()
        self.start_words = [w.strip().upper() for w in config.get("stt_start_words", "").split(",") if w.strip()]
        self.stop_words = [w.strip().upper() for w in config.get("stt_stop_words", "").split(",") if w.strip()]
        self.send_words = [w.strip().upper() for w in config.get("stt_send_words", "").split(",") if w.strip()]
        self.delete_words = [w.strip().upper() for w in config.get("stt_delete_words", "").split(",") if w.strip()]
        
        self.energy_threshold = config.get("stt_energy_threshold", 0.005)
        self.silence_timeout = config.get("stt_silence_timeout", 1.2)
        self.pre_roll = config.get("stt_pre_roll", 0.5)
        self.min_speech_duration = config.get("stt_min_speech_duration", 0.4)
        
        self.device = config.get("stt_device", "")
        if self.device == "": self.device = None

    def load_models(self):
        if not sherpa_onnx or not sd:
            raise ImportError("sherpa-onnx or sounddevice is not installed.")
            
        if not self.whisper_recognizer:
            whisper_dir = os.path.join(REPO_DIR, "sherpa-onnx-whisper-tiny.en")
            self.whisper_recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=f"{whisper_dir}/tiny.en-encoder.onnx",
                decoder=f"{whisper_dir}/tiny.en-decoder.onnx",
                tokens=f"{whisper_dir}/tiny.en-tokens.txt",
                num_threads=4
            )
            
        if not self.trigger_recognizer and self.mode == "hotword":
            model_dir = os.path.join(REPO_DIR, "sherpa-onnx-streaming-zipformer-en-2023-02-21")
            self.trigger_recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=f"{model_dir}/tokens.txt",
                encoder=f"{model_dir}/encoder-epoch-99-avg-1.int8.onnx",
                decoder=f"{model_dir}/decoder-epoch-99-avg-1.int8.onnx",
                joiner=f"{model_dir}/joiner-epoch-99-avg-1.int8.onnx",
                num_threads=2,
                sample_rate=self.SAMPLE_RATE,
                feature_dim=80,
                decoding_method="modified_beam_search",
                max_active_paths=14
            )

    def start_hotword(self):
        if self.mode == "hotword": return True
        self.stop()
        try:
            self.mode = "hotword"
            self.load_models()
            self.is_running = True
            self.audio_queue = queue.Queue()
            self.thread = threading.Thread(target=self._hotword_loop, daemon=True)
            self.thread.start()
            return True
        except Exception as e:
            self.mode = None
            if self.log_callback: self.log_callback(f"[bold red]STT Error:[/bold red] {e}")
            return False

    def start_smart_mic(self):
        if self.mode == "smart": return True
        self.stop()
        try:
            self.mode = "smart"
            self.load_models()
            self.is_running = True
            self.audio_queue = queue.Queue()
            self.thread = threading.Thread(target=self._smart_mic_loop, daemon=True)
            self.thread.start()
            return True
        except Exception as e:
            self.mode = None
            if self.log_callback: self.log_callback(f"[bold red]STT Error:[/bold red] {e}")
            return False

    def stop(self):
        self.is_running = False
        self.mode = None

    def _flush_whisper(self, audio_buffer, words_to_strip):
        """Helper to transcribe the buffer and strip trigger words."""
        if not audio_buffer: return ""
        audio_data = np.concatenate(audio_buffer).flatten()
        w_stream = self.whisper_recognizer.create_stream()
        w_stream.accept_waveform(self.SAMPLE_RATE, audio_data)
        self.whisper_recognizer.decode_stream(w_stream)
        
        text = w_stream.result.text.strip()
        for word in words_to_strip:
            text = re.sub(rf'\b{word}\b', '', text, flags=re.IGNORECASE)
        return text.strip(" .,")

    def _hotword_loop(self):
        is_recording = False
        audio_buffer = []
        trigger_stream = self.trigger_recognizer.create_stream()

        def audio_callback(indata, frames, time, status):
            if self.is_running: self.audio_queue.put(indata.copy())
        with AUDIO_LOCK:
            input_stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE, 
                channels=1, 
                dtype="float32", 
                device=self.device, 
                callback=audio_callback
            )

        try:
            with input_stream:
                while self.is_running:
                    try: chunk = self.audio_queue.get(timeout=0.5)
                    except queue.Empty: continue
                        
                    trigger_stream.accept_waveform(self.SAMPLE_RATE, chunk.flatten())
                    while self.trigger_recognizer.is_ready(trigger_stream):
                        self.trigger_recognizer.decode_stream(trigger_stream)
                    
                    partial_text = self.trigger_recognizer.get_result(trigger_stream).upper()

                    if not is_recording:
                        if any(word in partial_text for word in self.start_words):
                            is_recording = True
                            if self.tts_manager: self.tts_manager.stop_all_audio()
                            if self.log_callback: self.log_callback("[dim green]🎙️ Trigger word detected. Dictation started...[/dim green]")
                            self.trigger_recognizer.reset(trigger_stream)
                    else:
                        audio_buffer.append(chunk)

                        # 2. Delete Keyword (Removes last appended chunk)
                        if any(word in partial_text for word in self.delete_words):
                            audio_buffer = []
                            self.callback("", action="delete")
                            if self.log_callback: self.log_callback("[dim red]🗑️ Last sentence deleted.[/dim red]")
                            self.trigger_recognizer.reset(trigger_stream)

                        # 3. Stop Keyword (Ends dictation session without sending)
                        elif any(word in partial_text for word in self.stop_words):
                            is_recording = False
                            text = self._flush_whisper(audio_buffer, self.stop_words)
                            if text: self.callback(text, action="append")
                            audio_buffer = []
                            if self.log_callback: self.log_callback("[dim yellow]🔇 Dictation paused. Review your input.[/dim yellow]")
                            self.trigger_recognizer.reset(trigger_stream)

                        # 4. Send/Execute Keyword (Instantly fires the prompt)
                        elif any(word in partial_text for word in self.send_words):
                            is_recording = False
                            text = self._flush_whisper(audio_buffer, self.send_words)
                            self.callback(text, action="submit")
                            audio_buffer = []
                            self.trigger_recognizer.reset(trigger_stream)

                        # 5. Natural Pause (Appends to input box, keeps listening)
                        elif self.trigger_recognizer.is_endpoint(trigger_stream):
                            text = self._flush_whisper(audio_buffer, [])
                            if text: self.callback(text, action="append")
                            audio_buffer = []
                            self.trigger_recognizer.reset(trigger_stream)

        except Exception as e:
            self.is_running = False
            self.mode = None

    def _smart_mic_loop(self):
        chunk_samples = int(self.SAMPLE_RATE * self.CHUNK_DURATION)
        silence_chunks_limit = int(self.silence_timeout / self.CHUNK_DURATION)
        preroll_chunks_limit = int(self.pre_roll / self.CHUNK_DURATION)
        
        audio_buffer = []
        preroll_buffer = collections.deque(maxlen=preroll_chunks_limit)
        
        is_speaking = False
        silence_counter = 0

        def audio_callback(indata, frames, time, status):
            if self.is_running: self.audio_queue.put(indata.copy())

        try:
            with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1, dtype="float32", blocksize=chunk_samples, device=self.device, callback=audio_callback):
                while self.is_running:
                    try: chunk = self.audio_queue.get(timeout=0.5)
                    except queue.Empty: continue
                        
                    rms = np.sqrt(np.mean(np.square(chunk)))
                    
                    if rms > self.energy_threshold:
                        if not is_speaking:
                            is_speaking = True
                            if self.tts_manager: self.tts_manager.stop_all_audio()
                            if self.log_callback: self.log_callback("[dim green]🎤 (Voice Detected)[/dim green]")
                            audio_buffer = list(preroll_buffer)
                        audio_buffer.append(chunk)
                        silence_counter = 0
                    else:
                        if is_speaking:
                            audio_buffer.append(chunk)
                            silence_counter += 1
                            if silence_counter >= silence_chunks_limit:
                                is_speaking = False
                                
                                full_audio = np.concatenate(audio_buffer).flatten()
                                audio_buffer = []
                                silence_counter = 0
                                
                                if len(full_audio) > (self.SAMPLE_RATE * self.min_speech_duration):
                                    text = self._flush_whisper([full_audio], [])
                                    if text:
                                        self.callback(text, action="append") # Append to UI so you can edit
                        else:
                            preroll_buffer.append(chunk)
        except Exception as e:
            self.is_running = False
            self.mode = None