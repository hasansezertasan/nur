from typing import TYPE_CHECKING

from nur.providers.cargo_make import CargoMakeProvider
from nur.providers.composer import ComposerProvider
from nur.providers.deno import DenoProvider
from nur.providers.just import JustProvider
from nur.providers.make import MakeProvider
from nur.providers.mise import MiseProvider
from nur.providers.npm import NpmProvider
from nur.providers.pdm import PdmProvider
from nur.providers.poe import PoeProvider
from nur.providers.task import TaskfileProvider
from nur.providers.xc import XcProvider

if TYPE_CHECKING:
    from nur.models import Provider

PROVIDERS: list[Provider] = [
    NpmProvider(),
    DenoProvider(),
    ComposerProvider(),
    MakeProvider(),
    PdmProvider(),
    PoeProvider(),
    JustProvider(),
    TaskfileProvider(),
    MiseProvider(),
    CargoMakeProvider(),
    XcProvider(),
]

__all__ = [
    "PROVIDERS",
    "CargoMakeProvider",
    "ComposerProvider",
    "DenoProvider",
    "JustProvider",
    "MakeProvider",
    "MiseProvider",
    "NpmProvider",
    "PdmProvider",
    "PoeProvider",
    "TaskfileProvider",
    "XcProvider",
]
