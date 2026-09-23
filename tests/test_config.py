from pathlib import Path

from coursera_notes.config import load_config


def test_config_loads_project_values_and_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "coursera-notes.toml"
    config_file.write_text(
        "[coursera]\nresolution='540p'\nlanguage='fr'\nworkers=3\n"
        "[visuals]\nhash_distance=4\n[output]\nroot='./notes'\n"
    )

    config = load_config(config_file)

    assert config.coursera.resolution == "540p"
    assert config.coursera.language == "fr"
    assert config.coursera.workers == 3
    assert config.visuals.hash_distance == 4
    assert config.visuals.scene_threshold == 30
    assert config.output.root == Path("notes")


def test_missing_config_returns_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")

    assert config.coursera.resolution == "720p"
    assert config.output.root == Path("output")
