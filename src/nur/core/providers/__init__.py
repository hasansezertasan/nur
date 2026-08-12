from typing import TYPE_CHECKING

from nur.core.providers.cargo_make import CargoMakeProvider
from nur.core.providers.composer import ComposerProvider
from nur.core.providers.deno import DenoProvider
from nur.core.providers.just import JustProvider
from nur.core.providers.make import MakeProvider
from nur.core.providers.mise import MiseProvider
from nur.core.providers.npm import NpmProvider
from nur.core.providers.pdm import PdmProvider
from nur.core.providers.poe import PoeProvider
from nur.core.providers.task import TaskfileProvider
from nur.core.providers.xc import XcProvider

if TYPE_CHECKING:
    from nur.core.models import Provider

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
