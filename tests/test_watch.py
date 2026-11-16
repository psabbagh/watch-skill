"""Offline tests for watch.py. Run: python3 -m unittest discover -s tests

Downloading, captions and picture taking were checked live on real videos; these tests cover the choices
made around them and need no network or video tools.
"""
import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = fn(*args)
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        env = {"LEARNING_LIBRARY": str(self.root / "lib"), "LEARNING_SKILLS_CACHE": str(self.root / "cache")}
        self.old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        import library
        import media
        import watch
        importlib.reload(library)
        importlib.reload(media)
        self.watch = importlib.reload(watch)

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


def thumb(seed: int, changed: float = 0.0, base: int = None) -> bytes:
    """A 64x36 grey thumbnail: a fixed random picture for `seed`, with a share of it changed to a second one."""
    import random
    n = 64 * 36
    a = random.Random(seed).choices(range(256), k=n)
    if changed:
        b = random.Random(seed + 1000).choices(range(256), k=n)
        cut = int(n * changed)
        a = b[:cut] + a[cut:]
    return bytes(a)


class ChooseTimes(Base):
    def test_cuts_are_scenes_and_the_picture_is_the_finished_state(self):
        scan = [(0, thumb(1)), (4, thumb(1)), (20, thumb(2)), (24, thumb(2)), (40, thumb(3))]
        self.assertEqual(self.watch.choose_times(scan, 50, "normal", []), [4, 24, 40])

    def test_a_slow_build_counts_once_it_adds_up(self):
        # Each keyframe changes only 2 percent from the last, but by 30 seconds a third of the picture is new.
        scan = [(t, thumb(1, changed=t / 90)) for t in range(0, 31, 2)]
        times = self.watch.choose_times(scan, 31, "normal", [])
        self.assertGreater(len(times), 1)

    def test_blank_screens_and_repeated_shots_are_skipped(self):
        scan = [(0, bytes(64 * 36)), (10, thumb(1)), (30, thumb(2)), (50, thumb(1)), (70, thumb(3))]
        self.assertEqual(self.watch.choose_times(scan, 80, "normal", []), [10, 30, 70])

    def test_long_stretches_get_a_picture(self):
        scan = [(t, thumb(1)) for t in range(0, 600, 10)]
        times = self.watch.choose_times(scan, 600, "normal", [])
        self.assertEqual(len(times), 4)  # one long scene, plus one every three minutes

    def test_never_more_than_the_detail_allows(self):
        scan = [(t, thumb(t)) for t in range(0, 1800, 4)]
        for detail, most in (("light", 15), ("normal", 40), ("full", 100)):
            self.assertLessEqual(len(self.watch.choose_times(scan, 1800, detail, [])), most, detail)

    def test_times_asked_for_are_always_kept(self):
        times = self.watch.choose_times([(0, thumb(1)), (5, thumb(1))], 10, "light", [3.5])
        self.assertIn(3.5, times)

    def test_no_keyframes(self):
        self.assertEqual(self.watch.choose_times([], 0, "normal", [7]), [7])

    def test_longest_gaps(self):
        self.assertEqual(self.watch.longest_gaps([0, 30, 300, 330], 400), ["0:00:30 to 0:05:00"])

    def test_parse_time(self):
        self.assertEqual(self.watch.parse_time("1:02:03"), 3723)
        self.assertEqual(self.watch.parse_time("4:32"), 272)
        self.assertEqual(self.watch.parse_time("83"), 83)


class Source(Base):
    def test_pictures_sit_between_the_words_at_their_time(self):
        folder = self.root / "w"
        folder.mkdir()
        meta = {"title": "T", "source": "https://e.com/v", "how": "captions", "show": "Chan"}
        rows = [(0, "hello"), (10, "first slide"), (40, "second part")]
        self.watch.write_source(folder, meta, rows, [(0, "x/000000.jpg"), (35, "x/000035.jpg")])
        text = (folder / "source.md").read_text()
        order = [text.index(s) for s in ("[picture 0:00:00: frames/000000.jpg]", "hello first slide",
                                         "[picture 0:00:35: frames/000035.jpg]", "second part")]
        self.assertEqual(order, sorted(order))

    def test_silent_video_says_so(self):
        folder = self.root / "w"
        folder.mkdir()
        self.watch.write_source(folder, {"title": "T", "source": "s", "how": "no spoken words"}, [], [(0, "x/0.jpg")])
        self.assertIn("No spoken words", (folder / "source.md").read_text())


class Refusals(Base):
    def setUp(self):
        super().setUp()
        self.watch.ensure_frames = lambda: None  # no tools needed to refuse

    def test_audio_file_points_to_listen(self):
        f = self.root / "talk.mp3"
        f.write_bytes(b"x")
        code, out = quiet(self.watch.main, ["open", str(f)])
        self.assertEqual(code, 1)
        self.assertIn("listen skill", out)

    def test_spotify(self):
        code, out = quiet(self.watch.main, ["open", "https://open.spotify.com/episode/x"])
        self.assertEqual(code, 1)
        self.assertIn("Spotify", out)

    def test_bad_times(self):
        code, out = quiet(self.watch.main, ["open", "https://e.com/v", "--at", "soon"])
        self.assertEqual(code, 1)

    def test_not_a_link_or_file(self):
        code, out = quiet(self.watch.main, ["open", "some words"])
        self.assertEqual(code, 1)


class Dates(Base):
    def test_video_site_dates_are_saved_as_iso(self):
        import library
        folder = library.working() / "v-1"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text('{"source": "https://e.com/v", "date": "20171005"}')
        (folder / "notes.md").write_text("---\ntitle: V\nsource: x\nkind: video\ntopics: t\none_line: o\n---\n\nbody\n")
        code, out = quiet(library.main, ["save", "v-1"])
        self.assertEqual(code, 0, out)
        self.assertIn("published: 2017-10-05", (library.library() / "notes" / "v-1.md").read_text())


class More(Base):
    def test_more_needs_an_open_video(self):
        code, out = quiet(self.watch.main, ["more", "nothing-here", "--at", "1:00"])
        self.assertEqual(code, 1)
        self.assertIn("watch.py open", out)


class SharedFiles(unittest.TestCase):
    def test_shared_files_match_the_other_skills(self):
        """library.py is shared by listen, read and watch, media.py by listen and watch; a change goes to all."""
        checked = 0
        for name, others in (("library.py", ("listen", "read")), ("media.py", ("listen",))):
            mine = (SCRIPTS / name).read_text()
            for other in others:
                theirs = SCRIPTS.parent.parent / other / "scripts" / name
                if theirs.exists():
                    checked += 1
                    self.assertEqual(mine, theirs.read_text(), f"{other}/scripts/{name} differs; copy it across")

        if not checked:
            self.skipTest("the sibling skills are not in this repository")

if __name__ == "__main__":
    unittest.main()
