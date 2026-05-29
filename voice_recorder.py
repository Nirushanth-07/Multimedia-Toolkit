import sounddevice as sd
from scipy.io.wavfile import write

def record_audio(filename, duration, fs=44100):
    try:
        print(f"Recording for {duration} seconds...")
        recording = sd.rec(int(duration * fs), samplerate=fs, channels=1)
        sd.wait()
        write(filename, fs, recording)
        print(f"Recording saved as {filename}")
    except:
        print("Unknown Error")

if __name__ == "__main__":
    m = input("Enter the time duration of the recording in minutes: ")
    file_name = "my_recording.wav"

    try:
        seconds = float(m) * 60 
        record_audio(file_name, seconds)
    except:
        print("Duration --> Invalid value")