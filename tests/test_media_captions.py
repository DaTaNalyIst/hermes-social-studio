import pytest

from socialstudio import captions
from socialstudio import media as M


def test_instagram_image_aspect_is_clamped(media, tmp_path):
    out = M.make_image(media / "img3.png", "jpeg_ig", tmp_path / "ig.jpg")  # 5:1 panorama
    i = M.probe(out)
    assert 1.85 <= i.width / i.height <= 1.92 and i.width <= 1440


def test_story_canvas_and_bluesky_size(media, tmp_path):
    s = M.probe(M.make_image(media / "img0.png", "jpeg_story", tmp_path / "s.jpg"))
    assert (s.width, s.height) == (1080, 1920)
    b = M.make_image(media / "img1.png", "jpeg_bsky", tmp_path / "b.jpg")
    assert b.stat().st_size <= 950_000


def test_vertical_video_from_landscape_gets_audio_and_30fps(media, tmp_path):
    out = M.make_video(media / "landscape.mp4", "mp4_vertical", tmp_path / "v.mp4")
    i = M.probe(out)
    assert (i.width, i.height) == (1080, 1920) and i.audio
    import json, subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                        "-of", "json", str(out)], capture_output=True, text=True)
    assert json.loads(r.stdout)["streams"][0]["r_frame_rate"] == "30/1"


def test_target_checks(media):
    with pytest.raises(M.MediaError, match="at most 90"):
        M.check_target("facebook", "reel", [media / "long.mp4"])
    with pytest.raises(M.MediaError, match="1-4 images"):
        M.check_target("bluesky", "images", [media / f"img{i}.png" for i in range(4)] + [media / "img0.png"])
    with pytest.raises(M.MediaError, match="needs a video"):
        M.check_target("youtube", "short", [media / "img0.png"])
    assert M.check_target("instagram", "reel", [media / "long.mp4"])  # 120 s is fine for IG Reels


def test_variants_are_shared(media, tmp_path):
    vm = M.VariantMaker("p_test_000000", tmp_path)
    a = vm.variant(media / "portrait.mp4", "mp4_vertical")
    b = vm.variant(media / "portrait.mp4", "mp4_vertical")
    assert a is b and a["sha256"] and a["name"].startswith("p_test_000000__mp4_vertical")


def test_caption_rules():
    assert captions.problems("instagram", "hi " + " ".join(f"#t{i}" for i in range(6)), max_tags=5)
    assert not captions.problems("instagram", "hi #a #b", max_tags=5)
    assert captions.problems("threads", "x" * 501)
    assert captions.problems("threads", "two tags #a #b")
    assert captions.problems("youtube", "desc", title="")
    assert captions.problems("youtube", "desc <b>", title="ok")
    assert captions.problems("telegram", "x" * 1100, has_media=True)
    assert not captions.problems("telegram", "x" * 1100, has_media=False)
    assert captions.graphemes("👍🏽👨‍👩‍👧🇰🇪é") == 4
    assert captions.problems("bluesky", "a" * 301) and not captions.problems("bluesky", "é" * 300)


def test_compose_respects_hashtag_cap():
    text = captions.compose("Kenyan coffee #coffee", ["kenya", "barista", "farm", "harvest", "morning", "extra"],
                            "instagram", 5)
    assert len(captions.hashtags_in(text)) == 5
    assert captions.compose("Hello", ["a", "b"], "threads").count("#") == 1
