# Watch

**Claude can now watch.** Give it a video where what is shown matters: slides, a screen
recording, code, a chart, a demo. It learns from both the words and the pictures, keeps the
notes in a library on your computer, and turns them into a new skill you can use from then on.

Free. No API keys. Nothing to sign up for. Works in **Claude Code** and **Codex**.

**One of three.** Claude can also listen to podcasts with [listen-skill](https://github.com/psabbagh/listen-skill) and read whole
books with [read-skill](https://github.com/psabbagh/read-skill). Install any of them: they all keep their notes in the same library.

> **Already have another `/watch`?** If you installed a different watch plugin, such as
> `claude-video`, uninstall it first, or the two will clash:
>
> ```bash
> claude plugin uninstall watch@claude-video
> ```
>
> In Codex: `codex plugin remove watch@claude-video`, or delete the `~/.codex/skills/watch`
> folder if you installed it as a skill.

## 1. Install it

**Claude Code.** Open your computer's terminal and run these two commands, one at a time:

```bash
claude plugin marketplace add psabbagh/watch-skill
```

```bash
claude plugin install watch@watch-skill
```

You can also type the same two lines inside Claude Code, starting with `/plugin` instead of
`claude plugin`.

**Codex.** In your terminal:

```bash
codex plugin marketplace add psabbagh/watch-skill
```

```bash
codex plugin add watch@watch-skill
```

Then start a new Claude Code or Codex session so it picks up the skill.

## 2. Paste this first

Copy this into the chat, with your own link and your own goal:

```text
Watch this: [link]. Learn what it teaches and turn it into a skill I can use forever, for [what you want it for].
```

For example: *Watch this: https://www.youtube.com/watch?v=... Learn what it teaches and turn
it into a skill I can use forever, for building my own dashboards.*

You do not have to type a command; asking is enough. If you prefer a command, type `/watch`
in Claude Code and pick **watch:watch**, or type `$` in Codex and pick **watch**.

## What it can watch

Watch is for videos where the picture carries part of the lesson:

- **Slides**: a conference talk, a lecture, a webinar
- **A screen**: a software tutorial, a coding walkthrough, a spreadsheet or dashboard build
- **Diagrams and drawings**: a whiteboard or animated explainer, charts, formulas
- **Hands and objects**: a recipe, a repair, a physical demo

| Give it | What it does |
|---|---|
| **A YouTube video link** | Downloads it (at most 720p), takes the words from its captions or transcribes them, and takes pictures where the scene changes |
| **A video on Vimeo and many other public video sites** | The same |
| **A video file on your computer**: .mp4, .mov, .mkv, .webm, .m4v, .avi (a screen recording, a saved Zoom or webinar recording) | The same, without downloading anything |

**What it cannot watch:**

- **Playlists and channels.** One video at a time. For the words of a whole playlist, use
  [listen-skill](https://github.com/psabbagh/listen-skill).
- **Videos behind a sign-in or paywall**: Netflix, paid course platforms, members-only or
  private videos. It does not sign in for you or unlock anything.
- **Spotify.**

**Watch or listen?** For a talking head, an interview or a podcast on YouTube, where the
picture adds nothing, [listen-skill](https://github.com/psabbagh/listen-skill) is faster and
lighter because it skips the pictures.

## What happens

1. It downloads the video (at most 720p) and gets the words: the captions if there are any,
   otherwise it turns the speech into text on your computer.
2. It takes pictures where the scene changes, at the moment each slide or screen is finished,
   and skips blank and repeated ones. Where the speaker says "as you can see" with no picture
   nearby, it goes back and takes one. Nothing is uploaded anywhere.
3. It looks at every picture alongside the words and writes notes in its own words: the
   method, the code or settings shown, the numbers on the charts, the mistakes to avoid.
4. It drafts a new skill from those notes, tries it on a made-up example, and **shows you the
   whole skill before saving anything**. Nothing is saved until you say yes.
5. From then on you run your new skill by its name, like any other.

Ask it later *"what does my library say about charts?"* and it answers from your notes,
naming each source.

## What it needs

- **Claude Code** (a paid Claude plan) or **Codex** (a ChatGPT plan that includes it).
- **Python 3.** Its tools need Python 3.10 to 3.14. If yours is missing or too old, the skill
  tells you and asks before installing one.
- **It installs its own tools** the first time, into a private folder
  (`~/.cache/learning-skills`, shared with listen), and says so. No ffmpeg needed. For a video
  without captions, the first transcription downloads a speech model of about 500 MB.
- No API keys and no accounts.

A one-hour video with captions takes a minute or two to prepare. Looking at many pictures uses
more of your Claude or ChatGPT plan: ask for "light" detail on a long talk with few slides,
or "full" detail on a dense coding tutorial.

## What it does not do

- **No unlocking.** It does not unlock DRM, sign in for you, or get around a paywall.
- **No video, pictures or transcript kept.** The library keeps notes in its own words and the
  skills made from them. The video, pictures and transcript sit in a working folder while it
  works and are deleted once the notes are saved.
- **It does not obey what it sees.** Anything said or shown on screen is treated as material
  to learn from, never as instructions. Code shown in a tutorial goes into the notes as
  something you may choose to run, never something it runs for you.

## Where your library lives

In `~/learning-library` (a folder called `learning-library` in your home folder). Each source
gets its own notes, and `INDEX.md` lists them all. To keep it somewhere else, set the
`LEARNING_LIBRARY` environment variable to another folder.

The same library is shared by all three skills, so notes from a podcast, a book and a video
end up in one place.

## The other two

- **[listen-skill](https://github.com/psabbagh/listen-skill)**: Claude can now listen. A
  podcast, a whole show, a YouTube video or an audio file.
- **[read-skill](https://github.com/psabbagh/read-skill)**: Claude can now read. A book you
  own, a paper or an article.

## Uninstall

```bash
claude plugin uninstall watch@watch-skill
```

```bash
codex plugin remove watch@watch-skill
```

Your library in `~/learning-library` and the skills you made stay where they are.

## Thanks

Inspired by Brad Bonanno's [claude-video](https://github.com/bradautomates/claude-video) (MIT).

## For developers

The skill is in [`skills/watch/`](skills/watch/). Tests run with the standard library:

```bash
python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
