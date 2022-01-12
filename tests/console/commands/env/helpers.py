from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Union

from poetry.core.semver.version import Version


if TYPE_CHECKING:
    from poetry.core.version.pep440.version import PEP440Version

VERSION_3_7_1 = Version.parse("3.7.1")


def build_venv(path: Union[Path, str], **_: Any) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def check_output_wrapper(
    version: "PEP440Version" = VERSION_3_7_1,
) -> Callable[[str, Any, Any], str]:
    def check_output(cmd: str, *_: Any, **__: Any) -> str:
        if "sys.version_info[:3]" in cmd:
            return version.text
        elif "sys.version_info[:2]" in cmd:
            return f"{version.major}.{version.minor}"
        else:
            return str(Path("/prefix"))

    return check_output
