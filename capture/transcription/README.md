# Transcriber

Turns lecture videos (`.mp4`) into timestamped text with Whisper.

## Requirements

- The Python dependencies in the repository's `requirements.txt` (includes `whisper-timestamped`)
- `ffmpeg`: `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Debian/Ubuntu

## Usage

`my_transcribe.py` walks through every `data/course_*/videos/` folder. It extracts the audio from each `.mp4` with ffmpeg, transcribes it, and splits the result into sentence chunks with timestamps. Crawl the videos first (enable `videos` in `capture/crawling/main.py`). Run it from the repository root:

```bash
python -m capture.transcription.my_transcribe --model-size small
```

The transcripts go to `data/course_<id>/videos/transcribed_videos/`. Larger Whisper models (`medium`, `large`) give better transcripts and run slower. The script logs finished videos in `transcribed_videos/transformation_log.json`, so a second run skips them.

## Credits

The transcriber started from the Programmierpraktikum MVA project [tutor_ai](https://github.com/Programmierpraktikum-MVA/tutor_ai) by Maximilian Hans (m.hans@tu-berlin.de), released under the MIT License.
