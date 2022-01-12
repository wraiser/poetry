from typing import TYPE_CHECKING
from typing import List

import pytest
import tomlkit

from poetry.core.toml.file import TOMLFile


if TYPE_CHECKING:
    from pathlib import Path

    from cleo.testers.command_tester import CommandTester
    from pytest_mock import MockerFixture

    from poetry.utils.env import MockEnv
    from tests.types import CommandTesterFactory


@pytest.fixture
def venv_activate_37(venv_cache: "Path", venv_name: str) -> None:
    envs_file = TOMLFile(venv_cache / "envs.toml")
    doc = tomlkit.document()
    doc[venv_name] = {"minor": "3.7", "patch": "3.7.0"}
    envs_file.write(doc)


@pytest.fixture
def tester(command_tester_factory: "CommandTesterFactory") -> "CommandTester":
    return command_tester_factory("env list")


def test_none_activated(
    tester: "CommandTester",
    venvs_in_cache_dirs: List[str],
    mocker: "MockerFixture",
    env: "MockEnv",
):
    mocker.patch("poetry.utils.env.EnvManager.get", return_value=env)
    tester.execute()
    expected = "\n".join(venvs_in_cache_dirs).strip()
    assert expected == tester.io.fetch_output().strip()


def test_activated(
    tester: "CommandTester",
    venvs_in_cache_dirs: List[str],
    venv_cache: "Path",
    venv_activate_37: None,
):
    tester.execute()
    expected = (
        "\n".join(venvs_in_cache_dirs).strip().replace("py3.7", "py3.7 (Activated)")
    )
    assert expected == tester.io.fetch_output().strip()


def test_in_project_venv(tester: "CommandTester", venvs_in_project_dir: List[str]):
    tester.execute()
    expected = ".venv (Activated)\n"
    assert expected == tester.io.fetch_output()
