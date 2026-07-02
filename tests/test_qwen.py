import os
import sys
import time
import numpy as np
import wave

try:
    import sherpa_onnx
except ImportError:
    print("Error: sherpa-onnx is not installed.")
    sys.exit(1)

try:
    import sounddevice as sd
except ImportError:
    print("Error: sounddevice is not installed. (pip install sounddevice)")
    sys.exit(1)

# ==========================================
# CONFIGURATION
# ==========================================
# Update this path to point to your downloaded Qwen3 ONNX model folder.
# It should be the folder containing the 9 ONNX sub-models.
MODEL_DIR = "./Qwen3-TTS-0.6B-INT8"


def save_wav(filename, samples, sample_rate):
    """Saves a float32 numpy array to a 16-bit PCM WAV file."""
    # Convert float32[-1.0, 1.0] to int16 PCM
    samples_int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(filename, 'w') as wf:
        wf.setnchannels(1)           # Mono
        wf.setsampwidth(2)           # 2 bytes = 16 bit
        wf.setframerate(sample_rate)
        wf.writeframes(samples_int16.tobytes())


def main():
    print(f"Sherpa-ONNX version: {sherpa_onnx.__version__}")
    
    if not os.path.exists(MODEL_DIR):
        print(f"\n[!] Error: Model directory not found at {MODEL_DIR}")
        print("Please download the Qwen3 ONNX models and set MODEL_DIR correctly.")
        return

    print("\n[1/4] Initializing Qwen3-TTS model... (This may take a moment)")
    
    # Configure the offline TTS config specifically for Qwen3
    try:
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                qwen3=sherpa_onnx.OfflineTtsQwen3ModelConfig(
                    model_dir=MODEL_DIR
                ),
                num_threads=4,       # Match your system's performance cores
                debug=True,          # Keep True initially to debug ONNX loader logs
                provider="cpu"       # 'cpu', 'cuda', 'coreml', etc. based on your build
            )
        )
        tts = sherpa_onnx.OfflineTts(tts_config)
    except AttributeError as e:
        print(f"\n[!] Configuration Error: {e}")
        print("It looks like your sherpa-onnx doesn't have Qwen3 support built-in yet.")
        print("Make sure you compiled the 'develop' branch from the HeiSir2014 fork.")
        return

    print("[2/4] Model loaded successfully!")

    # Define the text you want to test
    text = "Hello! I am testing the new Qwen3 text-to-speech implementation via sherpa onnx. Does this sound natural?"
    print(f"\n[3/4] Generating audio for text: '{text}'")
    
    start_time = time.time()
    
    # sid=0 is the default speaker ID. speed=1.0 is default real-time speed.
    audio = tts.generate(text, sid=0, speed=1.0)
    
    if audio is None:
        print("\n[!] Failed to generate audio.")
        return

    elapsed_time = time.time() - start_time
    duration_sec = len(audio.samples) / audio.sample_rate
    rtf = elapsed_time / duration_sec  # Real-Time Factor
    
    print(f"\n[+] Generation complete in {elapsed_time:.2f} seconds!")
    print(f"    Sample rate  : {audio.sample_rate} Hz")
    print(f"    Audio length : {duration_sec:.2f} sec")
    print(f"    RTF          : {rtf:.3f}x (Lower than 1.0 means faster than real-time)")

    # Save to disk
    output_filename = "qwen3_test_output.wav"
    save_wav(output_filename, audio.samples, audio.sample_rate)
    print(f"\n[+] Saved raw output to '{output_filename}'")

    # Playback the generated audio array natively
    print("[4/4] Playing audio back...")
    try:
        sd.play(audio.samples, audio.sample_rate)
        sd.wait()  # Block until the audio finishes playing
        print("Playback finished.")
    except Exception as e:
        print(f"Failed to play audio using sounddevice: {e}")


if __name__ == "__main__":
    main()