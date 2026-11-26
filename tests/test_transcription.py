"""Test dei metadati video e del servizio di trascrizione."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from study_manager.database import Database
from study_manager.services.transcription import (
    DEFAULT_MODEL,
    MODEL_CATALOG,
    TranscriptBlock,
    TranscriptionError,
    build_extract_command,
    build_transcript_text,
    default_transcript_name,
    format_timestamp,
    model_is_available,
    model_path,
    rename_transcript_file,
    transcript_filename,
    wav_duration,
)


class TimestampTests(unittest.TestCase):
    def test_format_timestamp(self) -> None:
        self.assertEqual(format_timestamp(0), "00:00")
        self.assertEqual(format_timestamp(5.9), "00:05")
        self.assertEqual(format_timestamp(65.9), "01:05")
        self.assertEqual(format_timestamp(3599), "59:59")
        self.assertEqual(format_timestamp(3600), "60:00")

    def test_transcript_filename(self) -> None:
        self.assertEqual(transcript_filename("Lezione: 1?.txt"), "Lezione 1.txt")
        self.assertEqual(transcript_filename("  "), "Trascrizione.txt")
        self.assertEqual(
            default_transcript_name(Path("C:/video/Lezione 01.mp4")),
            "Lezione 01.txt",
        )


class TranscriptTextTests(unittest.TestCase):
    def test_timestamped_blocks_and_header(self) -> None:
        text = build_transcript_text(
            "Lezione di prova",
            Path("C:/video/lezione.mp4"),
            "Biologia",
            65,
            "base-q5_1",
            "it",
            [
                TranscriptBlock(0, 4, "  Prima   parte. "),
                TranscriptBlock(65, 70, "Seconda parte."),
            ],
            created_at="2026-10-21 10:00:00",
        )
        self.assertIn("# Titolo: Lezione di prova", text)
        self.assertIn("# Materia: Biologia", text)
        self.assertIn("# Durata: 01:05", text)
        self.assertIn("[00:00] Prima parte.", text)
        self.assertIn("[01:05] Seconda parte.", text)
        self.assertTrue(text.endswith("\n"))

    def test_empty_transcript_has_placeholder(self) -> None:
        text = build_transcript_text(
            "Senza parlato",
            "video.mp4",
            "",
            10,
            "base-q5_1",
            "it",
            [],
        )
        self.assertIn("[00:00] (Nessun parlato riconosciuto.)", text)
        self.assertIn("# Materia: Senza materia", text)


class MediaCommandTests(unittest.TestCase):
    def test_ffmpeg_command_extracts_mono_16khz_wav(self) -> None:
        command = build_extract_command("ffmpeg", "video.mp4", "audio.wav")
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-vn", command)
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-c:a") + 1], "pcm_s16le")
        self.assertEqual(command[-1], "audio.wav")

    def test_wav_duration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(16000)
                target.writeframes(b"\x00\x00" * 16000)
            self.assertAlmostEqual(wav_duration(path), 1.0, places=3)


class TranscriptFileTests(unittest.TestCase):
    def test_rename_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "originale.txt"
            source.write_text("testo", encoding="utf-8")
            renamed = rename_transcript_file(source, "nuovo nome", root)
            self.assertEqual(renamed.name, "nuovo nome.txt")
            self.assertFalse(source.exists())
            self.assertTrue(renamed.exists())
            collision = root / "esiste.txt"
            collision.write_text("altro", encoding="utf-8")
            with self.assertRaises(TranscriptionError):
                rename_transcript_file(renamed, "esiste", root)
            with self.assertRaises(TranscriptionError):
                rename_transcript_file(root / "manca.txt", "x", root)

    def test_model_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = model_path(folder, "base-q5_1")
            self.assertEqual(target.name, MODEL_CATALOG["base-q5_1"]["filename"])
            self.assertFalse(model_is_available(folder, "base-q5_1"))
            target.write_bytes(b"x")
            self.assertFalse(model_is_available(folder, "base-q5_1"))
            with self.assertRaises(TranscriptionError):
                model_path(folder, "sconosciuto")


class VideoDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")

    def tearDown(self) -> None:
        self.db.close_current_thread()
        self.temp.cleanup()

    def test_video_lesson_lifecycle(self) -> None:
        lesson_id = self.db.add_video_lesson(
            "Lezione 1",
            r"C:\video\lezione1.mp4",
            "Biologia",
            "lezione1.txt",
            "base-q5_1",
            "it",
        )
        lesson = self.db.get_video_lesson(lesson_id)
        self.assertIsNotNone(lesson)
        assert lesson is not None
        self.assertEqual(lesson["status"], "ready")
        self.assertEqual(lesson["subject"], "Biologia")
        self.assertEqual(lesson["transcript_path"], "")

        self.db.update_video_lesson_metadata(lesson_id, "Lezione aggiornata", "Chimica")
        self.db.set_video_lesson_job(lesson_id, "processing", 0.42)
        lesson = self.db.get_video_lesson(lesson_id)
        assert lesson is not None
        self.assertEqual(lesson["title"], "Lezione aggiornata")
        self.assertEqual(lesson["status"], "processing")
        self.assertAlmostEqual(lesson["progress"], 0.42)

        self.db.complete_video_lesson(
            lesson_id,
            r"C:\trascrizioni\lezione.txt",
            "lezione.txt",
            125.5,
            "base-q5_1",
            "it",
        )
        lesson = self.db.get_video_lesson(lesson_id)
        assert lesson is not None
        self.assertEqual(lesson["status"], "completed")
        self.assertEqual(lesson["progress"], 1)
        self.assertEqual(lesson["transcript_name"], "lezione.txt")
        self.assertAlmostEqual(lesson["duration_seconds"], 125.5)

        self.db.update_video_transcript_path(
            lesson_id, r"C:\trascrizioni\rinominata.txt", "rinominata.txt"
        )
        lesson = self.db.get_video_lesson(lesson_id)
        assert lesson is not None
        self.assertEqual(lesson["transcript_name"], "rinominata.txt")

        self.db.delete_video_lesson(lesson_id)
        self.assertIsNone(self.db.get_video_lesson(lesson_id))

    def test_video_doubts_and_notes(self) -> None:
        lesson_id = self.db.add_video_lesson("Lezione", "a.mp4", "Biologia")
        lesson = self.db.get_video_lesson(lesson_id)
        assert lesson is not None
        self.assertEqual((lesson["doubts"], lesson["notes"]), ("[]", ""))
        self.db.update_video_lesson_study(lesson_id, [{"text": "[01:02] perché?", "card": False}], "[00:10] nota")
        lesson = self.db.get_video_lesson(lesson_id)
        assert lesson is not None
        self.assertIn("perché", lesson["doubts"])
        self.assertEqual(lesson["notes"], "[00:10] nota")

    def test_model_catalog_has_pros_and_cons(self) -> None:
        self.assertIn(DEFAULT_MODEL, MODEL_CATALOG)
        for info in MODEL_CATALOG.values():
            self.assertTrue(info["pros"] and info["cons"])

    def test_recommend_model_by_hardware(self) -> None:
        from study_manager.services.transcription import recommend_model

        self.assertEqual(recommend_model(4, 15.9), "small-q5_1")
        self.assertEqual(recommend_model(8, 32), "large-v3-turbo-q5_0")
        self.assertEqual(recommend_model(6, 16), "medium-q5_0")
        self.assertEqual(recommend_model(2, 4), "base-q5_1")
        self.assertEqual(recommend_model(2, 2), "tiny-q5_1")

    def test_estimate_seconds_uses_measurements(self) -> None:
        from study_manager.services.transcription import estimate_seconds
        from study_manager.ui.video_view import format_eta

        self.assertAlmostEqual(estimate_seconds("small-q5_1", 3600, 4, {}), 900)
        # Large misurato 2x piu' lento della stima: anche Small viene raddoppiato.
        self.assertAlmostEqual(estimate_seconds("small-q5_1", 3600, 4, {"large-v3-turbo-q5_0": 1.3}), 1800)
        self.assertAlmostEqual(estimate_seconds("small-q5_1", 100, 4, {"small-q5_1": 0.5}), 50)
        self.assertEqual(format_eta(150), "~2 min")
        self.assertEqual(format_eta(4800), "~1 h 20 min")

    def test_progress_suffix_estimates_finish(self) -> None:
        from datetime import datetime

        from study_manager.ui.video_view import progress_suffix

        now = datetime(2026, 1, 1, 10, 0)
        self.assertEqual(progress_suffix(0.25, 600, now), " (25% · fine stimata 10:30)")
        self.assertEqual(progress_suffix(0.01, 600, now), " (1%)")

    def test_filter_and_subject_tags(self) -> None:
        self.db.add_video_lesson("A", "a.mp4", "Biologia")
        self.db.add_video_lesson("B", "b.mp4", "Matematica")
        self.db.add_video_lesson("C", "c.mp4", "biologia")
        self.assertEqual(len(self.db.list_video_lessons()), 3)
        filtered = self.db.list_video_lessons("BIOLOGIA")
        self.assertEqual(len(filtered), 2)
        tags = [tag.casefold() for tag in self.db.list_subject_tags()]
        self.assertIn("biologia", tags)
        self.assertIn("matematica", tags)

    def test_interrupted_jobs_recovery(self) -> None:
        ready_id = self.db.add_video_lesson("Pronta", "a.mp4", "Biologia")
        processing_id = self.db.add_video_lesson("In corso", "b.mp4", "Biologia")
        self.db.set_video_lesson_job(processing_id, "processing", 0.5)
        recovered = self.db.recover_interrupted_video_jobs()
        self.assertEqual(recovered, 1)
        self.assertEqual(self.db.get_video_lesson(ready_id)["status"], "ready")
        lesson = self.db.get_video_lesson(processing_id)
        assert lesson is not None
        self.assertEqual(lesson["status"], "error")
        self.assertIn("interrotta", lesson["error"].lower())


if __name__ == "__main__":
    unittest.main()
